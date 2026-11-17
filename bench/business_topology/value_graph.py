"""SQL-backed business value graphs layered above mechanical topology.

The topology excavation finds populations and candidate business nouns.  This
module deliberately changes altitude: it groups a bounded domain, identifies
likely fact streams and reusable dimensions, asks a model for a small number
of consequential metrics, and then proves every surviving definition by
executing its event projection in a read-only transaction.

Each metric owns one canonical SQL projection with exactly three columns:

``occurred_at timestamptz, value numeric, dimensions jsonb``

That narrow relation is sufficient to derive time series, contribution views,
and period-over-period change graphs without inventing a second metric DSL.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from .contracts import ContractError
from .worker import _json_object


VALUE_GRAPH_SCHEMA_VERSION = "rvbbit.business-value.graph.v1"
VALUE_GRAPH_WORKER_VERSION = "business-value-graph-v2"
VALUE_DOMAIN_PLAN_SCHEMA_VERSION = "rvbbit.business-value.domain-plan.v3"
FAMILY_CONSOLIDATION_SCHEMA_VERSION = "rvbbit.business-semantic.family-consolidation.v1"
_METRIC_KINDS = {"outcome", "component", "driver"}
_UNITS = {"currency", "count", "quantity"}
_RELATIONSHIPS = {"adds", "subtracts", "multiplies", "divides", "drives", "contributes_to"}
_CONCEPT_KINDS = {"object", "event", "process", "classification", "measure", "place", "actor"}
_SOURCE_ROLES = {"event_stream", "transaction", "reference", "dimension", "snapshot", "context"}
_SEMANTIC_RELATIONSHIPS = {
    "joins_to",
    "parallel_to",
    "comparable_to",
    "describes",
    "classifies",
    "located_at",
    "performed_by",
    "part_of",
    "informs",
}
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_MEASURE_NAME_RE = re.compile(
    r"(amount|revenue|sales|price|cost|profit|margin|quantity|qty|count|score|total)",
    re.I,
)
_TIME_NAME_RE = re.compile(r"(date|time|_at$|month|year|period)", re.I)
_ID_NAME_RE = re.compile(
    r"(^id$|_id$|^id_|_key$|^key$|_ref$|_uuid$|^uuid$|_number$|_no$|_code$)",
    re.I,
)
_LABEL_NAME_RE = re.compile(
    r"(name|title|label|description|category|subcategory|segment|status|type|channel|region|country)",
    re.I,
)
_RATE_NAME_RE = re.compile(r"(unit_(price|cost)|price|rate|ratio|percent|percentage|margin|score)", re.I)


def _semantic_role(column: str, type_family: str) -> str:
    if type_family == "time" or _TIME_NAME_RE.search(column):
        return "time"
    if _ID_NAME_RE.search(column):
        return "key"
    if type_family == "text" and _LABEL_NAME_RE.search(column):
        return "business_label"
    if type_family == "number" and _RATE_NAME_RE.search(column):
        return "non_additive_rate"
    if type_family == "number" and _MEASURE_NAME_RE.search(column):
        return "additive_measure_candidate"
    if type_family in {"text", "boolean"}:
        return "categorical_attribute"
    return "attribute"


def _title(value: str) -> str:
    return " ".join(part for part in re.split(r"[_\W]+", value) if part).title()


def _relation_parts(value: str) -> tuple[str, str]:
    parts = value.replace('"', "").split(".", 1)
    if len(parts) != 2 or not all(parts):
        raise ContractError(f"value graph relation must be schema-qualified: {value}")
    return parts[0], parts[1]


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _relation_sql(schema: str, relation: str) -> str:
    return f"{_quote_identifier(schema)}.{_quote_identifier(relation)}"


def load_domain_packet(
    conn: Any,
    schema: str,
    relations: Sequence[str],
    *,
    topology_summaries: Sequence[Mapping[str, Any]] = (),
    domain_key: str | None = None,
) -> dict[str, Any]:
    """Build bounded, value-free metadata for one coherent domain unit."""

    relation_names = sorted(
        relation
        for relation_schema, relation in map(_relation_parts, relations)
        if relation_schema == schema and not relation.lower().startswith("_dlt_")
    )
    if not relation_names:
        raise ContractError(f"domain {schema} has no eligible relations")

    rows = conn.execute(
        """
        SELECT c.relname AS relation_name,
               greatest(c.reltuples,0)::bigint AS estimated_rows,
               a.attnum AS ordinal_position,
               a.attname AS column_name,
               pg_catalog.format_type(a.atttypid,a.atttypmod) AS data_type,
               t.typcategory AS type_category,
               s.n_distinct,
               s.null_frac
          FROM pg_class c
          JOIN pg_namespace n ON n.oid=c.relnamespace
          JOIN pg_attribute a ON a.attrelid=c.oid
          JOIN pg_type t ON t.oid=a.atttypid
          LEFT JOIN pg_stats s
            ON s.schemaname=n.nspname
           AND s.tablename=c.relname
           AND s.attname=a.attname
         WHERE n.nspname=%s
           AND c.relname=ANY(%s)
           AND c.relkind IN ('r','p','m')
           AND a.attnum>0 AND NOT a.attisdropped
         ORDER BY c.relname,a.attnum
        """,
        (schema, relation_names),
    ).fetchall()

    grouped: dict[str, dict[str, Any]] = {}
    key_usage: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        relation = str(row[0])
        column = str(row[3])
        if column.lower().startswith("_dlt_"):
            continue
        entry = grouped.setdefault(
            relation,
            {
                "relation": f"{schema}.{relation}",
                "estimated_rows": int(row[1] or 0),
                "columns": [],
            },
        )
        category = str(row[5] or "")
        type_family = (
            "number" if category == "N" else
            "time" if category == "D" else
            "boolean" if category == "B" else
            "text" if category in {"S", "V"} else
            "other"
        )
        column_row = {
            "name": column,
            "data_type": str(row[4]),
            "type_family": type_family,
            "semantic_role": _semantic_role(column, type_family),
            "distinct_estimate": float(row[6]) if row[6] is not None else None,
            "null_fraction": float(row[7]) if row[7] is not None else None,
            "measure_hint": bool(type_family == "number" and _MEASURE_NAME_RE.search(column) and not _ID_NAME_RE.search(column)),
            "time_hint": bool(type_family == "time" or _TIME_NAME_RE.search(column)),
            "key_hint": bool(_ID_NAME_RE.search(column)),
        }
        entry["columns"].append(column_row)
        if column_row["key_hint"]:
            key_usage[column].append(f"{schema}.{relation}")

    relation_rows = list(grouped.values())
    if not relation_rows:
        raise ContractError(f"domain {schema} metadata could not be resolved")

    # The RVBBIT catalog has already paid to profile and describe these
    # relations.  Reuse its value-free descriptions instead of making every
    # semantic pass rediscover the same table/column shape from names alone.
    # Catalog column documents may contain sampled examples, so only retain
    # the stable description before that section.
    catalog_docs: dict[tuple[str, str | None], str] = {}
    try:
        catalog_available = bool(
            conn.execute(
                "SELECT to_regclass('rvbbit.catalog_docs') IS NOT NULL"
            ).fetchone()[0]
        )
        if catalog_available:
            for catalog_row in conn.execute(
                """
                SELECT rel_name,col_name,doc
                  FROM rvbbit.catalog_docs
                 WHERE schema_name=%s
                   AND rel_name=ANY(%s)
                   AND kind IN ('db_table','db_column')
                 ORDER BY rel_name,col_name NULLS FIRST
                """,
                (schema, relation_names),
            ).fetchall():
                doc = re.split(r"\s+Examples:\s*", str(catalog_row[2] or ""), 1)[0]
                catalog_docs[(str(catalog_row[0]), catalog_row[1])] = doc[:1200]
    except Exception:  # pragma: no cover - older appliances simply lack the catalog
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()

    for relation in relation_rows:
        relation_name = str(relation["relation"]).split(".", 1)[1]
        relation["catalog_summary"] = catalog_docs.get((relation_name, None))
        for column in relation["columns"]:
            column["catalog_summary"] = catalog_docs.get(
                (relation_name, str(column["name"]))
            )

    shared_keys = [
        {"column": column, "relations": sorted(set(key_relations))}
        for column, key_relations in sorted(key_usage.items())
        if len(set(key_relations)) > 1
    ]
    shared_key_names = {row["column"] for row in shared_keys}
    fact_candidates: list[dict[str, Any]] = []
    for relation in relation_rows:
        columns = relation["columns"]
        measures = [column["name"] for column in columns if column["measure_hint"]]
        # A time column that planner statistics know to be entirely NULL (for
        # example an unused image_last_updated) can never anchor an event
        # stream; leave it out so neither the model nor the deterministic
        # fallback builds a metric that yields zero events.
        times = [
            column["name"] for column in columns
            if column["time_hint"]
            and not (
                column.get("null_fraction") is not None
                and float(column["null_fraction"]) >= 1.0
            )
        ]
        keys = [column["name"] for column in columns if column["name"] in shared_key_names]
        score = len(measures) * 4 + min(len(keys), 6) * 2 + min(len(times), 2) * 3
        if measures and times:
            fact_candidates.append(
                {
                    "relation": relation["relation"],
                    "score": score,
                    "measure_columns": measures,
                    "time_columns": times,
                    "shared_key_columns": keys,
                }
            )
    fact_candidates.sort(key=lambda item: (-int(item["score"]), str(item["relation"])))

    relation_by_name = {str(row["relation"]): row for row in relation_rows}
    join_opportunities: list[dict[str, Any]] = []
    for shared_key in shared_keys:
        participants = []
        for qualified_relation in shared_key["relations"]:
            relation = relation_by_name.get(qualified_relation)
            if not relation:
                continue
            label_columns = [
                column["name"]
                for column in relation["columns"]
                if column.get("semantic_role") == "business_label"
            ][:8]
            participants.append(
                {
                    "relation": qualified_relation,
                    "estimated_rows": relation["estimated_rows"],
                    "business_label_columns": label_columns,
                }
            )
        join_opportunities.append(
            {
                "shared_key": shared_key["column"],
                "participants": participants,
                "purpose": (
                    "candidate cross-source identifier; compare coverage and "
                    "cardinality before claiming an exact join"
                ),
            }
        )

    foreign_key_rows = conn.execute(
        """
        SELECT source.relname AS source_relation,
               target.relname AS target_relation,
               ARRAY(
                   SELECT source_attribute.attname
                     FROM unnest(constraint_row.conkey) WITH ORDINALITY key(attnum,ordinal)
                     JOIN pg_attribute source_attribute
                       ON source_attribute.attrelid=constraint_row.conrelid
                      AND source_attribute.attnum=key.attnum
                    ORDER BY key.ordinal
               ) AS source_columns,
               ARRAY(
                   SELECT target_attribute.attname
                     FROM unnest(constraint_row.confkey) WITH ORDINALITY key(attnum,ordinal)
                     JOIN pg_attribute target_attribute
                       ON target_attribute.attrelid=constraint_row.confrelid
                      AND target_attribute.attnum=key.attnum
                    ORDER BY key.ordinal
               ) AS target_columns
          FROM pg_constraint constraint_row
          JOIN pg_class source ON source.oid=constraint_row.conrelid
          JOIN pg_namespace source_schema ON source_schema.oid=source.relnamespace
          JOIN pg_class target ON target.oid=constraint_row.confrelid
          JOIN pg_namespace target_schema ON target_schema.oid=target.relnamespace
         WHERE constraint_row.contype='f'
           AND source_schema.nspname=%s
           AND target_schema.nspname=%s
           AND source.relname=ANY(%s)
           AND target.relname=ANY(%s)
         ORDER BY source.relname,target.relname,constraint_row.conname
        """,
        (schema, schema, relation_names, relation_names),
    ).fetchall()
    foreign_key_relationships = [
        {
            "source_relation": f"{schema}.{row[0]}",
            "target_relation": f"{schema}.{row[1]}",
            "source_columns": [str(value) for value in (row[2] or [])],
            "target_columns": [str(value) for value in (row[3] or [])],
        }
        for row in foreign_key_rows
    ]

    safe_summaries = [
        {
            "name": str(summary.get("name") or ""),
            "sources": [str(value) for value in summary.get("sources", [])],
            "nodes": [
                {
                    "name": str(node.get("name") or ""),
                    "kind": str(node.get("kind") or ""),
                    "description": str(node.get("description") or "")[:500],
                }
                for node in summary.get("nodes", [])
                if isinstance(node, Mapping)
            ][:24],
        }
        for summary in topology_summaries
        if isinstance(summary, Mapping)
    ]
    return {
        "schema_version": "rvbbit.business-value.domain-packet.v1",
        "domain_key": domain_key or schema,
        "relations": relation_rows,
        "shared_keys": shared_keys[:80],
        "join_opportunities": join_opportunities[:80],
        "foreign_key_relationships": foreign_key_relationships[:80],
        "fact_candidates": fact_candidates[:12],
        "topology_summaries": safe_summaries[:40],
        "privacy": {"raw_values": False, "value_hashes": False},
    }


def load_value_system_packet(
    conn: Any,
    relations: Sequence[str],
    *,
    topology_summaries: Sequence[Mapping[str, Any]] = (),
    system: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one executable packet while keeping physical schemas incidental.

    PostgreSQL metadata queries remain schema-bounded, but a semantic value
    system is allowed to span deployment boundaries. The merged packet also
    recomputes shared-key evidence across schemas; matching names are only
    candidates, never asserted joins.
    """

    packets: list[dict[str, Any]] = []
    for schema, schema_relations in group_relations_by_schema(relations).items():
        relation_set = set(schema_relations)
        packets.append(
            load_domain_packet(
                conn,
                schema,
                schema_relations,
                topology_summaries=[
                    summary
                    for summary in topology_summaries
                    if any(source in relation_set for source in summary.get("sources", []))
                ],
                domain_key=str(system.get("system_key") or ""),
            )
        )
    if not packets:
        raise ContractError("value system has no eligible relations")

    relation_rows = [row for packet in packets for row in packet.get("relations", [])]
    key_usage: dict[str, list[str]] = defaultdict(list)
    relation_by_name = {str(row["relation"]): row for row in relation_rows}
    for relation in relation_rows:
        for column in relation.get("columns", []):
            if column.get("key_hint"):
                key_usage[str(column["name"])].append(str(relation["relation"]))
    shared_keys = [
        {"column": column, "relations": sorted(set(key_relations))}
        for column, key_relations in sorted(key_usage.items())
        if len(set(key_relations)) > 1
    ]
    join_opportunities: list[dict[str, Any]] = []
    for shared_key in shared_keys:
        participants: list[dict[str, Any]] = []
        for qualified_relation in shared_key["relations"]:
            relation = relation_by_name[qualified_relation]
            participants.append(
                {
                    "relation": qualified_relation,
                    "estimated_rows": int(relation.get("estimated_rows") or 0),
                    "business_label_columns": [
                        str(column["name"])
                        for column in relation.get("columns", [])
                        if column.get("semantic_role") == "business_label"
                    ][:8],
                }
            )
        join_opportunities.append(
            {
                "shared_key": str(shared_key["column"]),
                "participants": participants,
                "purpose": "candidate cross-source identifier; require exact evidence before joining",
            }
        )

    semantic_model = {
        "business_domain": {
            "domain_key": str(system.get("domain_key") or ""),
            "name": str(system.get("domain_name") or ""),
            "summary": str(system.get("domain_summary") or ""),
        },
        "value_system_key": str(system.get("system_key") or ""),
        "value_system_name": str(system.get("name") or ""),
        "value_system_summary": str(system.get("summary") or ""),
        "relationship_mode": str(system.get("relationship_mode") or ""),
        "concepts": list(system.get("concepts") or []),
        "processes": list(system.get("processes") or []),
        "source_memberships": list(system.get("memberships") or []),
        "semantic_relationships": list(system.get("relationships") or []),
        "analytical_questions": list(system.get("analytical_questions") or []),
        "gaps": list(system.get("gaps") or []),
    }
    return {
        "schema_version": "rvbbit.business-value.domain-packet.v2",
        # Storage still calls this domain_key. In v2 it is explicitly the
        # stable value-system identity, while business_domain is a parent.
        "domain_key": str(system.get("system_key") or ""),
        "relations": relation_rows,
        "shared_keys": shared_keys[:120],
        "join_opportunities": join_opportunities[:120],
        "foreign_key_relationships": [
            row
            for packet in packets
            for row in packet.get("foreign_key_relationships", [])
        ][:120],
        "fact_candidates": sorted(
            [row for packet in packets for row in packet.get("fact_candidates", [])],
            key=lambda item: (-int(item.get("score") or 0), str(item.get("relation") or "")),
        )[:20],
        "topology_summaries": [
            row for packet in packets for row in packet.get("topology_summaries", [])
        ][:60],
        "semantic_model": semantic_model,
        "privacy": {"raw_values": False, "value_hashes": False},
    }


def load_value_domain_planning_packet(
    conn: Any,
    relations: Sequence[str],
    *,
    topology_summaries: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Distill per-source evidence before asking a strong model to group domains."""

    eligible_relations = [
        str(relation)
        for relation in relations
        if _relation_parts(str(relation))[0].lower() != "cubes"
        and not any(part.lower().startswith("_dlt_") for part in _relation_parts(str(relation)))
    ]
    grouped = group_relations_by_schema(eligible_relations)
    relation_cards: list[dict[str, Any]] = []
    relationship_evidence: list[dict[str, Any]] = []
    summaries_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for summary in topology_summaries:
        if not isinstance(summary, Mapping):
            continue
        safe_summary = {
            "name": str(summary.get("name") or "")[:180],
            "nodes": [
                {
                    "name": str(node.get("name") or "")[:180],
                    "kind": str(node.get("kind") or "")[:80],
                    "description": str(node.get("description") or "")[:180],
                }
                for node in summary.get("nodes", [])
                if isinstance(node, Mapping)
            ][:12],
        }
        for source in summary.get("sources", []):
            summaries_by_source[str(source)].append(safe_summary)

    for schema, schema_relations in grouped.items():
        packet = load_domain_packet(
            conn,
            schema,
            schema_relations,
            topology_summaries=(),
        )
        for relation in packet["relations"]:
            qualified = str(relation["relation"])
            columns = sorted(
                list(relation.get("columns", [])),
                key=lambda column: (
                    0 if column.get("semantic_role") in {
                        "time", "key", "business_label", "additive_measure_candidate",
                        "non_additive_rate",
                    } else 1,
                    str(column.get("name") or ""),
                ),
            )
            relation_cards.append(
                {
                    "relation": qualified,
                    "estimated_rows": int(relation.get("estimated_rows") or 0),
                    "catalog_summary": str(relation.get("catalog_summary") or "")[:700],
                    "columns": [
                        {
                            "name": str(column.get("name") or ""),
                            "type_family": str(column.get("type_family") or ""),
                            "semantic_role": str(column.get("semantic_role") or ""),
                            "catalog_summary": str(column.get("catalog_summary") or "")[:180],
                        }
                        for column in columns[:28]
                    ],
                    "topology_findings": sorted(
                        summaries_by_source.get(qualified, []),
                        key=lambda item: json.dumps(
                            item,
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                        ),
                    )[:4],
                }
            )
        relationship_evidence.extend(
            {"kind": "candidate_shared_identifier", **dict(item)}
            for item in packet.get("join_opportunities", [])
        )
        relationship_evidence.extend(
            {"kind": "foreign_key", **dict(item)}
            for item in packet.get("foreign_key_relationships", [])
        )

    # Imports frequently split one application across schemas or arrive as
    # constraint-free exports.  Repeated identifier-shaped names are useful
    # candidate linkage evidence even when PostgreSQL has no FK to expose.
    # Keep this explicitly weaker than a foreign key: the model may use it to
    # group sources, but must record uncertainty instead of inventing a join.
    global_key_usage: dict[str, list[str]] = defaultdict(list)
    for relation in relation_cards:
        for column in relation.get("columns", []):
            if column.get("semantic_role") == "key":
                global_key_usage[str(column.get("name") or "").lower()].append(
                    str(relation["relation"])
                )
    existing_shared_keys = {
        (
            str(item.get("shared_key") or item.get("column") or "").lower(),
            tuple(sorted(
                str(participant.get("relation") if isinstance(participant, Mapping) else participant)
                for participant in item.get("participants", item.get("relations", []))
            )),
        )
        for item in relationship_evidence
        if item.get("kind") == "candidate_shared_identifier"
    }
    for column, participants in sorted(global_key_usage.items()):
        unique_participants = sorted(set(participants))
        identity = (column, tuple(unique_participants))
        if len(unique_participants) < 2 or identity in existing_shared_keys:
            continue
        relationship_evidence.append(
            {
                "kind": "candidate_shared_identifier",
                "shared_key": column,
                "participants": [
                    {"relation": relation} for relation in unique_participants
                ],
                "purpose": (
                    "candidate cross-source identifier; compare coverage and "
                    "cardinality before claiming an exact join"
                ),
            }
        )
    return {
        "schema_version": "rvbbit.business-value.domain-planning-packet.v2",
        "relations": sorted(relation_cards, key=lambda item: str(item["relation"])),
        "relationship_evidence": relationship_evidence[:160],
        "privacy": {
            "raw_values": False,
            "value_hashes": False,
            "rvbbit_catalog_descriptions_reused": bool(
                any(item.get("catalog_summary") for item in relation_cards)
            ),
        },
    }


def _domain_plan_prompt(packet: Mapping[str, Any], max_relations_per_domain: int) -> str:
    return f"""TASK: Decide whether these already-analyzed sources form a coherent business topology, and if so, describe it.

There may be nothing here. Configuration tables, platform bookkeeping, disconnected fragments, or a
system you cannot identify from this packet are all legitimate findings. In that case return
`"domains": []`, list every relation in `unassigned_relations` with a concrete reason, and fill
`verdict` with `"status": "no_coherent_system"`. An honest empty result is a success; a domain
manufactured to satisfy the task is the failure mode. Never merge relations into a domain merely
so that the answer has one. You cannot browse, run tools, or consult files: everything you know
about these sources is in this packet. If a decision needs documentation or evidence you do not
have (a vendor's object model, what a connector's metadata table means), name it in `needs`
instead of guessing — that list is acted on separately.

This is semantic planning, not metric generation and not a table inventory. A business domain is a
stable organizational or operational area. A value system is an analytical view of an outcome or
process inside that area. A schema is only a deployment boundary and a table is only evidence.
Use the compact per-source topology findings first, then column roles and relationship evidence.
When `prior_semantic_evidence` is present, treat it as the preceding hypothesis plus executable
observations—not as another source inventory. Preserve a Value System's identity across ordinary
category drift. Exact complete dimension censuses may establish the full observed dictionary;
bounded samples never do. A category absent from the current comparison period can still be a valid
all-time member. Put contradictions in gaps or review the existing system instead of manufacturing a
new domain. Accepted `family_relationships` are durable semantic constraints: preserve accepted
sameness, extension, parallel-population, or separation decisions unless new evidence explicitly
contradicts them. Proposed relationships are review evidence, not established fact. Rejected
relationships must not be reused as consolidation guidance.

The same source may participate in several concepts or value systems. This is intentional: joins,
shared dimensions, reference data, and overlapping operational processes are real. Group related
value systems under a small number of durable business domains. Identify the business concepts and
processes before assigning sources. Distinguish exact joins from semantic comparability: only
`joins_to` may claim exact columns, while `parallel_to` or `comparable_to` describes analytical
alignment without pretending rows can be joined. A `candidate_shared_identifier` is naming and
shape evidence for grouping, not proof that rows match; preserve it as a gap or comparability
signal unless a declared foreign key or equally explicit evidence supports `joins_to`.

Do not create a domain per table or per city/vendor copy. Do not merge unrelated tables merely
because they share a schema or generic columns. A value system may contain at most
{max_relations_per_domain} source memberships, but it may cross schemas. Every input relation must
appear in at least one membership or in `unassigned_relations` with a concrete reason. Prefer
membership over omission. Use globally semantic stable snake_case keys; avoid schema, vendor,
staging, or city names unless geography is intrinsically the business concept.

Return exactly:
{{
  "domains": [{{
    "domain_key": "stable_semantic_key",
    "name": "business-readable domain name",
    "summary": "what belongs to this durable business area",
    "concepts": [{{
      "concept_key": "incident",
      "name": "Incident",
      "kind": "object" | "event" | "process" | "classification" | "measure" | "place" | "actor",
      "description": "evidence-backed meaning"
    }}],
    "processes": [{{
      "process_key": "incident_reporting",
      "name": "Incident reporting",
      "description": "what happens",
      "concept_keys": ["incident"]
    }}],
    "systems": [{{
      "system_key": "reported_incidents",
      "name": "Reported Incidents",
      "summary": "the analytical outcome or process this system explains",
      "relationship_mode": "joined" | "parallel" | "mixed" | "standalone",
      "memberships": [{{
        "relation": "schema.relation",
        "role": "event_stream" | "transaction" | "reference" | "dimension" | "snapshot" | "context",
        "concept_keys": ["incident"],
        "rationale": "why this source participates",
        "confidence": 0.0
      }}],
      "relationships": [{{
        "relationship": "joins_to" | "parallel_to" | "comparable_to" | "describes" | "classifies" | "located_at" | "performed_by" | "part_of" | "informs",
        "left_relation": "schema.relation or null",
        "right_relation": "schema.relation or null",
        "subject_concept_key": "concept key or null",
        "object_concept_key": "concept key or null",
        "left_columns": ["exact join column only"],
        "right_columns": ["exact join column only"],
        "rationale": "evidence and limits",
        "confidence": 0.0
      }}],
      "analytical_questions": ["useful question enabled by this system"],
      "gaps": ["missing denominator, linkage, taxonomy, coverage, or grain limitation"]
    }}]
  }}],
  "unassigned_relations": [{{"relation":"schema.relation","reason":"specific reason"}}],
  "verdict": {{"status": "coherent" | "partial" | "no_coherent_system", "reason": "one or two sentences on what these sources are, or are not"}},
  "needs": ["documentation or evidence that would change the decision, e.g. 'Heroku Connect _hcmeta table semantics'"]
}}

PLANNING PACKET:
{json.dumps(packet, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"""


_PLAN_VERDICT_STATUSES = {"coherent", "partial", "no_coherent_system"}


def _plan_verdict(value: Any) -> dict[str, Any]:
    """The model's own statement of whether a topology exists here. Optional
    in the answer, always present in the plan so receipts can be read later."""

    if not isinstance(value, Mapping):
        return {}
    status = str(value.get("status") or "").strip().lower()
    verdict: dict[str, Any] = {}
    if status in _PLAN_VERDICT_STATUSES:
        verdict["status"] = status
    reason = str(value.get("reason") or "").strip()
    if reason:
        verdict["reason"] = reason[:2000]
    return verdict


def _plan_needs(value: Any) -> list[str]:
    """Documentation or evidence the model said it lacked. Never acted on
    here — recorded so the recognition step can supply it next time."""

    if not isinstance(value, list):
        return []
    needs: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in needs:
            needs.append(text[:500])
        if len(needs) >= 24:
            break
    return needs


def normalize_value_domain_plan(
    packet: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    max_relations_per_domain: int = 8,
) -> dict[str, Any]:
    source = payload.get("result")
    raw = dict(source) if isinstance(source, Mapping) and "domains" not in payload else dict(payload)
    raw_domains = raw.get("domains")
    expected = {
        str(item.get("relation"))
        for item in packet.get("relations", [])
        if isinstance(item, Mapping) and item.get("relation")
    }
    verdict = _plan_verdict(raw.get("verdict"))
    needs = _plan_needs(raw.get("needs"))
    if not isinstance(raw_domains, list) or not raw_domains:
        # An honest "nothing coherent here" is a valid plan, not a contract
        # failure: the model must have said so explicitly, or accounted for
        # every relation in unassigned_relations. Anything else is still a
        # malformed answer.
        unassigned_raw = raw.get("unassigned_relations")
        unassigned_null: list[dict[str, Any]] = []
        if isinstance(unassigned_raw, list):
            for item in unassigned_raw:
                if not isinstance(item, Mapping):
                    continue
                relation = str(item.get("relation") or "").strip()
                if relation:
                    unassigned_null.append({
                        "relation": relation,
                        "reason": str(item.get("reason") or "").strip() or "not stated",
                    })
        accounted = {item["relation"] for item in unassigned_null}
        if verdict.get("status") != "no_coherent_system" and not (expected and expected <= accounted):
            raise ContractError(
                "value-domain plan requires at least one domain, or an explicit "
                "verdict.status of no_coherent_system with every relation in unassigned_relations"
            )
        for relation in sorted(expected - accounted):
            unassigned_null.append({"relation": relation, "reason": "no coherent system (unlisted)"})
        verdict.setdefault("status", "no_coherent_system")
        return {
            "schema_version": VALUE_DOMAIN_PLAN_SCHEMA_VERSION,
            "domains": [],
            "systems": [],
            "cross_system_candidates": [],
            "unassigned_relations": sorted(unassigned_null, key=lambda item: item["relation"]),
            "degenerate_relationship_drops": [],
            "out_of_scope_drops": [],
            "verdict": verdict,
            "needs": needs,
        }
    relation_columns = {
        str(item.get("relation")): {
            str(column.get("name"))
            for column in item.get("columns", [])
            if isinstance(column, Mapping) and column.get("name")
        }
        for item in packet.get("relations", [])
        if isinstance(item, Mapping) and item.get("relation")
    }
    covered: set[str] = set()
    keys: set[str] = set()
    system_keys: set[str] = set()
    model_cross_system_suggestions: list[dict[str, Any]] = []
    degenerate_relationship_drops: list[dict[str, Any]] = []
    out_of_scope_drops: list[dict[str, Any]] = []
    domains: list[dict[str, Any]] = []
    for index, item in enumerate(raw_domains):
        if not isinstance(item, Mapping):
            raise ContractError(f"value-domain plan item {index} must be an object")
        key = str(item.get("domain_key") or "").strip().lower()
        if not _KEY_RE.fullmatch(key) or key in keys:
            raise ContractError(f"value-domain plan has invalid or duplicate domain_key: {key}")
        concepts_raw = item.get("concepts", [])
        processes_raw = item.get("processes", [])
        systems_raw = item.get("systems", [])
        if not isinstance(concepts_raw, list) or not concepts_raw:
            raise ContractError(f"business domain {key} requires concepts")
        if not isinstance(processes_raw, list):
            raise ContractError(f"business domain {key} processes must be an array")
        if not isinstance(systems_raw, list) or not systems_raw:
            raise ContractError(f"business domain {key} requires value systems")
        concept_keys: set[str] = set()
        concepts: list[dict[str, Any]] = []
        for concept in concepts_raw[:32]:
            if not isinstance(concept, Mapping):
                raise ContractError(f"business domain {key} has an invalid concept")
            concept_key = str(concept.get("concept_key") or "").strip().lower()
            kind = str(concept.get("kind") or "")
            if not _KEY_RE.fullmatch(concept_key) or concept_key in concept_keys:
                raise ContractError(f"business domain {key} has an invalid or duplicate concept_key")
            if kind not in _CONCEPT_KINDS:
                raise ContractError(f"business concept {concept_key} has an invalid kind")
            concept_keys.add(concept_key)
            concepts.append({
                "concept_key": concept_key,
                "name": " ".join(str(concept.get("name") or _title(concept_key)).split())[:180],
                "kind": kind,
                "description": " ".join(str(concept.get("description") or "").split())[:1000],
            })
        processes: list[dict[str, Any]] = []
        process_keys: set[str] = set()
        for process in processes_raw[:24]:
            if not isinstance(process, Mapping):
                raise ContractError(f"business domain {key} has an invalid process")
            process_key = str(process.get("process_key") or "").strip().lower()
            referenced = sorted({str(value) for value in process.get("concept_keys", [])})
            if not _KEY_RE.fullmatch(process_key) or process_key in process_keys:
                raise ContractError(f"business domain {key} has an invalid or duplicate process_key")
            if any(value not in concept_keys for value in referenced):
                raise ContractError(f"business process {process_key} references an unknown concept")
            process_keys.add(process_key)
            processes.append({
                "process_key": process_key,
                "name": " ".join(str(process.get("name") or _title(process_key)).split())[:180],
                "description": " ".join(str(process.get("description") or "").split())[:1000],
                "concept_keys": referenced[:16],
            })
        systems: list[dict[str, Any]] = []
        for system in systems_raw[:16]:
            if not isinstance(system, Mapping):
                raise ContractError(f"business domain {key} has an invalid value system")
            system_key = str(system.get("system_key") or "").strip().lower()
            if not _KEY_RE.fullmatch(system_key) or system_key in system_keys:
                raise ContractError(f"value system has an invalid or duplicate system_key: {system_key}")
            memberships_raw = system.get("memberships", [])
            if not isinstance(memberships_raw, list) or not memberships_raw:
                raise ContractError(f"value system {system_key} requires source memberships")
            if len(memberships_raw) > max_relations_per_domain:
                raise ContractError(
                    f"value system {system_key} exceeds {max_relations_per_domain} source memberships"
                )
            memberships: list[dict[str, Any]] = []
            membership_relations: set[str] = set()
            dropped_member_relations: set[str] = set()
            for membership in memberships_raw:
                if not isinstance(membership, Mapping):
                    raise ContractError(f"value system {system_key} has an invalid membership")
                relation = str(membership.get("relation") or "")
                role = str(membership.get("role") or "")
                referenced = sorted({str(value) for value in membership.get("concept_keys", [])})
                if relation not in expected or relation in membership_relations:
                    # Models with prior semantic evidence in context sometimes
                    # reuse a familiar sibling-scope system and staff it with
                    # out-of-scope relations (field case 2026-09-01: a
                    # crime_feeds excavation minted traffic_stop_enforcement,
                    # a public-schema domain). A foreign or duplicate member
                    # is a cross-scope hypothesis, not in-scope evidence —
                    # drop it with a receipt note instead of failing the run.
                    duplicate = relation in membership_relations
                    if not duplicate:
                        # A duplicate's relation is still a valid member;
                        # only truly foreign relations poison later edges.
                        dropped_member_relations.add(relation)
                    out_of_scope_drops.append({
                        "system_key": system_key,
                        "kind": "duplicate_membership" if duplicate
                                else "out_of_scope_membership",
                        "relation": relation,
                    })
                    continue
                if role not in _SOURCE_ROLES:
                    raise ContractError(f"value system {system_key} membership {relation} has an invalid role")
                if not referenced or any(value not in concept_keys for value in referenced):
                    raise ContractError(f"value system {system_key} membership {relation} needs known concepts")
                confidence = _finite_score(membership.get("confidence"), f"membership {relation}.confidence")
                membership_relations.add(relation)
                covered.add(relation)
                memberships.append({
                    "relation": relation,
                    "role": role,
                    "concept_keys": referenced[:16],
                    "rationale": " ".join(str(membership.get("rationale") or "").split())[:1000],
                    "confidence": confidence,
                })
            if not memberships:
                # Every member was foreign or duplicate: the whole system is a
                # cross-scope hypothesis. Drop it, keep the excavation.
                out_of_scope_drops.append({
                    "system_key": system_key,
                    "kind": "system_without_in_scope_members",
                    "relation": None,
                })
                continue
            mode = str(system.get("relationship_mode") or "")
            if mode not in {"joined", "parallel", "mixed", "standalone"}:
                raise ContractError(f"value system {system_key} has invalid relationship_mode")
            if len(memberships) == 1:
                mode = "standalone"
            relationships_raw = system.get("relationships", [])
            if not isinstance(relationships_raw, list):
                raise ContractError(f"value system {system_key} relationships must be an array")
            relationships: list[dict[str, Any]] = []
            for relationship in relationships_raw[:32]:
                if not isinstance(relationship, Mapping):
                    continue
                predicate = str(relationship.get("relationship") or "")
                left = str(relationship.get("left_relation") or "") or None
                right = str(relationship.get("right_relation") or "") or None
                subject = str(relationship.get("subject_concept_key") or "") or None
                object_key = str(relationship.get("object_concept_key") or "") or None
                if predicate not in _SEMANTIC_RELATIONSHIPS:
                    raise ContractError(f"value system {system_key} has an invalid semantic relationship")
                if (left and left in dropped_member_relations) or (right and right in dropped_member_relations):
                    # The membership this edge leaned on was dropped as
                    # out-of-scope above; the edge goes with it, on record.
                    out_of_scope_drops.append({
                        "system_key": system_key,
                        "kind": "relationship_to_dropped_member",
                        "relation": left if left in dropped_member_relations else right,
                    })
                    continue
                if left and left not in membership_relations or right and right not in membership_relations:
                    weak_cross_system = (
                        predicate in {"parallel_to", "comparable_to"}
                        and left in expected
                        and right in expected
                    )
                    if weak_cross_system:
                        # Weak comparisons cannot live inside one value system, but
                        # they are useful hypotheses. Hoist them to the plan level;
                        # evidence-backed pairs collapse into the stronger catalog
                        # candidate below, while unsupported pairs remain explicitly
                        # marked needs_evidence. Exact joins still fail closed.
                        model_cross_system_suggestions.append({
                            "relationship": predicate,
                            "left_relation": str(left),
                            "right_relation": str(right),
                            "rationale": " ".join(
                                str(relationship.get("rationale") or "").split()
                            )[:1000],
                            "confidence": _finite_score(
                                relationship.get("confidence"),
                                f"relationship {predicate}.confidence",
                            ),
                        })
                        continue
                    raise ContractError(f"value system {system_key} relationship references a non-member source")
                if subject and subject not in concept_keys or object_key and object_key not in concept_keys:
                    raise ContractError(f"value system {system_key} relationship references an unknown concept")
                if not ((left and right and left != right) or (subject and object_key and subject != object_key)):
                    # A self-loop carries no relational information. Models
                    # occasionally emit one for single-source domains even
                    # after a correction round; dropping it (with a receipt
                    # note below) salvages the excavation instead of failing
                    # the whole run over a worthless edge. Field case
                    # 2026-09-01: reported_incidents failed both attempts on
                    # exactly this.
                    degenerate_relationship_drops.append({
                        "system_key": system_key,
                        "relationship": predicate,
                        "left_relation": left,
                        "right_relation": right,
                        "subject_concept_key": subject,
                        "object_concept_key": object_key,
                    })
                    continue
                left_columns = [str(value) for value in relationship.get("left_columns", [])][:8]
                right_columns = [str(value) for value in relationship.get("right_columns", [])][:8]
                if predicate == "joins_to" and (not left or not right or not left_columns or not right_columns):
                    raise ContractError(f"value system {system_key} exact join lacks source columns")
                if predicate == "joins_to" and (
                    any(column not in relation_columns.get(str(left), set()) for column in left_columns)
                    or any(column not in relation_columns.get(str(right), set()) for column in right_columns)
                ):
                    raise ContractError(f"value system {system_key} exact join uses unknown columns")
                if predicate != "joins_to":
                    left_columns, right_columns = [], []
                relationships.append({
                    "relationship": predicate,
                    "left_relation": left,
                    "right_relation": right,
                    "subject_concept_key": subject,
                    "object_concept_key": object_key,
                    "left_columns": left_columns,
                    "right_columns": right_columns,
                    "rationale": " ".join(str(relationship.get("rationale") or "").split())[:1000],
                    "confidence": _finite_score(
                        relationship.get("confidence"), f"relationship {predicate}.confidence"
                    ),
                })
            system_keys.add(system_key)
            systems.append({
                "system_key": system_key,
                "name": " ".join(str(system.get("name") or _title(system_key)).split())[:180],
                "summary": " ".join(str(system.get("summary") or "").split())[:1600],
                "relationship_mode": mode,
                "memberships": memberships,
                "relationships": relationships,
                "analytical_questions": [
                    " ".join(str(value).split())[:500]
                    for value in system.get("analytical_questions", [])[:12]
                    if str(value).strip()
                ],
                "gaps": [
                    " ".join(str(value).split())[:500]
                    for value in system.get("gaps", [])[:12]
                    if str(value).strip()
                ],
            })
        domains.append(
            {
                "domain_key": key,
                "name": " ".join(str(item.get("name") or _title(key)).split())[:180],
                "summary": " ".join(str(item.get("summary") or "").split())[:1600],
                "concepts": concepts,
                "processes": processes,
                "systems": systems,
            }
        )
        keys.add(key)
    raw_unassigned = raw.get("unassigned_relations", [])
    if not isinstance(raw_unassigned, list):
        raise ContractError("unassigned_relations must be an array")
    unassigned: list[dict[str, str]] = []
    unassigned_keys: set[str] = set()
    for item in raw_unassigned:
        if not isinstance(item, Mapping):
            raise ContractError("unassigned relation must be an object")
        relation = str(item.get("relation") or "")
        reason = " ".join(str(item.get("reason") or "").split())[:1000]
        if relation not in expected or relation in unassigned_keys or relation in covered or not reason:
            raise ContractError(f"invalid unassigned relation: {relation}")
        unassigned_keys.add(relation)
        unassigned.append({"relation": relation, "reason": reason})
    if covered | unassigned_keys != expected:
        missing = sorted(expected - covered - unassigned_keys)
        raise ContractError(f"value-domain plan does not account for every relation: {missing}")
    flattened = [
        {
            **system,
            "domain_key": str(domain["domain_key"]),
            "domain_name": str(domain["name"]),
            "domain_summary": str(domain["summary"]),
            "concepts": list(domain["concepts"]),
            "processes": list(domain["processes"]),
            "relations": sorted({str(row["relation"]) for row in system["memberships"]}),
        }
        for domain in domains
        for system in domain["systems"]
    ]
    system_domains = {
        str(system["system_key"]): str(domain["domain_key"])
        for domain in domains
        for system in domain["systems"]
    }
    relation_systems: dict[str, set[str]] = defaultdict(set)
    for system in flattened:
        for relation in system["relations"]:
            relation_systems[str(relation)].add(str(system["system_key"]))
    cross_system_candidates: list[dict[str, Any]] = []
    seen_candidates: set[tuple[str, tuple[str, ...]]] = set()
    for evidence in packet.get("relationship_evidence", []):
        if not isinstance(evidence, Mapping):
            continue
        if evidence.get("kind") != "candidate_shared_identifier":
            continue
        shared_key = str(evidence.get("shared_key") or evidence.get("column") or "").strip().lower()
        relations = sorted({
            str(participant.get("relation") if isinstance(participant, Mapping) else participant)
            for participant in evidence.get("participants", evidence.get("relations", []))
            if str(participant.get("relation") if isinstance(participant, Mapping) else participant) in expected
        })
        identity = (shared_key, tuple(relations))
        if not shared_key or len(relations) < 2 or identity in seen_candidates:
            continue
        system_refs = sorted({
            system_key
            for relation in relations
            for system_key in relation_systems.get(relation, set())
        })
        if len(system_refs) < 2:
            continue
        seen_candidates.add(identity)
        cross_system_candidates.append({
            "candidate_key": (
                f"shared_identifier:{shared_key}:"
                f"{hashlib.sha256(json.dumps(relations).encode()).hexdigest()[:12]}"
            ),
            "kind": "candidate_shared_identifier",
            "shared_key": shared_key,
            "relations": relations,
            "system_keys": system_refs,
            "domain_keys": sorted({system_domains[key] for key in system_refs}),
            "exact_join": False,
            "status": "needs_validation",
            "rationale": " ".join(str(
                evidence.get("purpose")
                or "Repeated identifier-shaped field; compare coverage and cardinality before claiming a join."
            ).split())[:1000],
        })
    evidence_relation_sets = {
        tuple(sorted(candidate["relations"]))
        for candidate in cross_system_candidates
    }
    seen_suggestions: set[tuple[str, tuple[str, ...]]] = set()
    for suggestion in model_cross_system_suggestions:
        relations = tuple(sorted({
            str(suggestion.get("left_relation") or ""),
            str(suggestion.get("right_relation") or ""),
        }))
        predicate = str(suggestion.get("relationship") or "")
        identity = (predicate, relations)
        if (
            len(relations) != 2
            or not all(relations)
            or relations in evidence_relation_sets
            or identity in seen_suggestions
        ):
            continue
        system_refs = sorted({
            system_key
            for relation in relations
            for system_key in relation_systems.get(relation, set())
        })
        if len(system_refs) < 2:
            continue
        seen_suggestions.add(identity)
        cross_system_candidates.append({
            "candidate_key": (
                f"model_relationship:{predicate}:"
                f"{hashlib.sha256(json.dumps(relations).encode()).hexdigest()[:12]}"
            ),
            "kind": "model_suggested_relationship",
            "relationship": predicate,
            "relations": list(relations),
            "system_keys": system_refs,
            "domain_keys": sorted({system_domains[key] for key in system_refs}),
            "exact_join": False,
            "status": "needs_evidence",
            "rationale": str(suggestion.get("rationale") or (
                "The model suggested a weak cross-system comparison; profile "
                "shared keys, coverage, and grain before accepting it."
            )),
            "confidence": suggestion.get("confidence"),
        })
    return {
        "schema_version": VALUE_DOMAIN_PLAN_SCHEMA_VERSION,
        "domains": sorted(domains, key=lambda item: str(item["domain_key"])),
        "systems": sorted(flattened, key=lambda item: str(item["system_key"])),
        "cross_system_candidates": sorted(
            cross_system_candidates, key=lambda item: str(item["candidate_key"])
        ),
        "unassigned_relations": sorted(unassigned, key=lambda item: item["relation"]),
        "verdict": verdict or {"status": "coherent"},
        "needs": needs,
        # No silent caps: self-loop relationships and out-of-scope
        # memberships the parser discarded stay visible in the plan so the
        # receipt records what was dropped.
        "degenerate_relationship_drops": degenerate_relationship_drops,
        "out_of_scope_drops": out_of_scope_drops,
    }


def discover_value_domains(
    packet: Mapping[str, Any],
    client: Any,
    *,
    max_relations_per_domain: int = 8,
    attempts: int = 2,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Use the strong judgment lane to turn compact source findings into domains."""

    prompt = _domain_plan_prompt(packet, max_relations_per_domain)
    receipts: list[dict[str, Any]] = []
    error: str | None = None
    prior: str | None = None
    for attempt in range(1, attempts + 1):
        user = prompt
        if error:
            user += f"\n\nCORRECTION REQUIRED:\n{error}\n\nPRIOR JSON:\n{(prior or '')[:16000]}"
        if progress_callback:
            progress_callback({"event": "planning_started", "attempt": attempt, "attempt_limit": attempts})
        try:
            content, raw_receipt = client.complete_json(
                system=(
                    "You are a business-domain architect. Reconcile source findings into a small "
                    "semantic topology with overlapping source membership, explicit concepts, "
                    "processes, value systems, exact joins, comparability, and gaps. Return JSON only."
                ),
                user=user,
                max_tokens=7_000,
            )
            receipt = dict(raw_receipt)
            receipt["attempt"] = attempt
            receipt["domain_plan_contract_version"] = VALUE_DOMAIN_PLAN_SCHEMA_VERSION
            receipts.append(receipt)
            prior = content
            plan = normalize_value_domain_plan(
                packet,
                _json_object(content),
                max_relations_per_domain=max_relations_per_domain,
            )
            if progress_callback:
                progress_callback(
                    {
                        "event": "planning_completed",
                        "attempt": attempt,
                        "attempt_limit": attempts,
                        "latency_ms": receipt.get("latency_ms"),
                        "model_version": receipt.get("model_version"),
                    }
                )
            return plan, {
                "schema_version": "rvbbit.business-value.domain-plan-receipt.v1",
                "generation_receipts": receipts,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001 - model/contract repair boundary
            error = f"{type(exc).__name__}: {exc}"[:4000]
            if progress_callback:
                progress_callback(
                    {
                        "event": "planning_failed",
                        "attempt": attempt,
                        "attempt_limit": attempts,
                        "error_class": type(exc).__name__,
                        "error": error,
                    }
                )
    raise ContractError(f"value-domain planning failed after {attempts} attempts: {error}")


_FAMILY_RELATIONSHIP_KINDS = {
    "same_family",
    "extension",
    "parallel_population",
    "alternative_definition",
    "material_contradiction",
    "distinct_family",
    "abstain",
}


def _family_consolidation_prompt(packet: Mapping[str, Any]) -> str:
    return f"""TASK: Adjudicate a small shortlist of Value System family relationships.

This is cross-family consolidation, not schema discovery and not metric generation. The source
Value System and each candidate are compact semantic dossiers assembled from already-modeled
graphs. Never assume that two systems are the same merely because they share a schema or broad
business domain. Conversely, a city, vendor, table, cube, grain, category slice, or DLT copy does
not deserve a new semantic identity by itself.

Choose exactly one relationship for every candidate:

- `same_family`: two presentations of the same coherent analytical system; perspectives, metric
  subsets, and materializations should share identity.
- `extension`: the source adds a defensible population, lookup, process stage, or coverage area to
  the target family without contradicting it.
- `parallel_population`: the same concepts/process are observed for separate agencies, regions,
  tenants, or periods and are comparable but not row-joinable.
- `alternative_definition`: similar outcome with a materially different grain, denominator,
  inclusion rule, or interpretation that must remain explicit.
- `material_contradiction`: the dossiers make incompatible claims about the same business meaning.
- `distinct_family`: related candidate blocking evidence exists, but these are legitimately
  different Value Systems.
- `abstain`: evidence is too weak or ambiguous.

`same_family` and `extension` are merge proposals, not permission to rewrite data or definitions.
Do not invent joins. Derived, staging, `_dlt`, or cube relations may support lineage but never
create a business family or business domain. Prefer the stable semantic key that excludes schema,
vendor, and geography unless geography is intrinsically the business concept. Cite only evidence
keys present on the candidate. Return every candidate exactly once and JSON only:

{{
  "decisions": [{{
    "candidate_id": "id from packet",
    "relationship_kind": "same_family | extension | parallel_population | alternative_definition | material_contradiction | distinct_family | abstain",
    "confidence": 0.0,
    "proposed_business_domain_key": "stable key or null",
    "proposed_business_domain_name": "business-readable name or null",
    "proposed_family_key": "stable Value System key or null",
    "proposed_family_name": "business-readable Value System name or null",
    "rationale": "specific explanation of sameness, extension, comparability, conflict, or separation",
    "evidence_refs": ["candidate evidence key"],
    "caveats": ["important uncertainty"]
  }}]
}}

COMPACT FAMILY PACKET:
{json.dumps(packet, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"""


def normalize_family_consolidations(
    packet: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    nested = payload.get("result")
    raw = dict(nested) if isinstance(nested, Mapping) and "decisions" not in payload else dict(payload)
    raw_decisions = raw.get("decisions")
    if not isinstance(raw_decisions, list):
        raise ContractError("family consolidation requires a decisions array")
    candidates = {
        str(candidate.get("candidate_id")): candidate
        for candidate in packet.get("candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("candidate_id")
    }
    if not candidates:
        raise ContractError("family consolidation packet requires candidates")
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_decision in raw_decisions:
        if not isinstance(raw_decision, Mapping):
            raise ContractError("family consolidation decision must be an object")
        candidate_id = str(raw_decision.get("candidate_id") or "")
        candidate = candidates.get(candidate_id)
        if candidate is None or candidate_id in seen:
            raise ContractError(f"family consolidation has unknown or duplicate candidate: {candidate_id}")
        relationship_kind = str(raw_decision.get("relationship_kind") or "")
        if relationship_kind not in _FAMILY_RELATIONSHIP_KINDS:
            raise ContractError(f"family consolidation has invalid relationship: {relationship_kind}")
        rationale = " ".join(str(raw_decision.get("rationale") or "").split())[:2000]
        if not rationale:
            raise ContractError(f"family consolidation {candidate_id} requires rationale")
        evidence = candidate.get("evidence")
        evidence = dict(evidence) if isinstance(evidence, Mapping) else {}
        raw_evidence_refs = raw_decision.get("evidence_refs", [])
        if not isinstance(raw_evidence_refs, list):
            raise ContractError(
                f"family consolidation {candidate_id}.evidence_refs must be an array"
            )
        evidence_refs = sorted({str(value) for value in raw_evidence_refs})
        if any(value not in evidence for value in evidence_refs):
            raise ContractError(f"family consolidation {candidate_id} cites unknown evidence")

        def optional_key(name: str) -> str | None:
            value = str(raw_decision.get(name) or "").strip().lower()
            if not value:
                return None
            if not _KEY_RE.fullmatch(value):
                raise ContractError(f"family consolidation {candidate_id} has invalid {name}")
            return value

        def optional_name(name: str) -> str | None:
            value = " ".join(str(raw_decision.get(name) or "").split())[:180]
            return value or None

        raw_caveats = raw_decision.get("caveats", [])
        if not isinstance(raw_caveats, list):
            raise ContractError(
                f"family consolidation {candidate_id}.caveats must be an array"
            )
        decisions.append({
            "candidate_id": candidate_id,
            "candidate_fingerprint": str(candidate.get("candidate_fingerprint") or candidate_id),
            "source_graph_id": str(packet.get("source", {}).get("graph_id") or ""),
            "target_graph_id": str(candidate.get("target", {}).get("graph_id") or ""),
            "relationship_kind": relationship_kind,
            "confidence": _finite_score(
                raw_decision.get("confidence"),
                f"family consolidation {candidate_id}.confidence",
            ),
            "proposed_business_domain_key": optional_key("proposed_business_domain_key"),
            "proposed_business_domain_name": optional_name("proposed_business_domain_name"),
            "proposed_family_key": optional_key("proposed_family_key"),
            "proposed_family_name": optional_name("proposed_family_name"),
            "rationale": rationale,
            "evidence_refs": evidence_refs,
            "caveats": [
                " ".join(str(value).split())[:500]
                for value in raw_caveats[:12]
                if str(value).strip()
            ],
            "candidate_evidence": evidence,
        })
        seen.add(candidate_id)
    missing = sorted(set(candidates) - seen)
    if missing:
        raise ContractError(f"family consolidation omitted candidates: {missing}")
    return {
        "schema_version": FAMILY_CONSOLIDATION_SCHEMA_VERSION,
        "source_graph_id": str(packet.get("source", {}).get("graph_id") or ""),
        "decisions": sorted(decisions, key=lambda item: item["candidate_id"]),
    }


def discover_family_consolidations(
    packet: Mapping[str, Any],
    client: Any,
    *,
    attempts: int = 2,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adjudicate one changed family against a deterministic compact shortlist."""

    prompt = _family_consolidation_prompt(packet)
    receipts: list[dict[str, Any]] = []
    error: str | None = None
    prior: str | None = None
    for attempt in range(1, attempts + 1):
        user = prompt
        if error:
            user += f"\n\nCORRECTION REQUIRED:\n{error}\n\nPRIOR JSON:\n{(prior or '')[:16000]}"
        if progress_callback:
            progress_callback({
                "event": "consolidation_started",
                "attempt": attempt,
                "attempt_limit": attempts,
            })
        try:
            content, raw_receipt = client.complete_json(
                system=(
                    "You are a semantic-family steward. Compare only the supplied compact "
                    "dossiers, make precision-first typed relationship decisions, and abstain "
                    "when evidence is insufficient. Return JSON only."
                ),
                user=user,
                max_tokens=4_500,
            )
            receipt = dict(raw_receipt)
            receipt["attempt"] = attempt
            receipt["family_consolidation_contract_version"] = FAMILY_CONSOLIDATION_SCHEMA_VERSION
            receipts.append(receipt)
            prior = content
            result = normalize_family_consolidations(packet, _json_object(content))
            if progress_callback:
                progress_callback({
                    "event": "consolidation_completed",
                    "attempt": attempt,
                    "attempt_limit": attempts,
                    "latency_ms": receipt.get("latency_ms"),
                    "model_version": receipt.get("model_version"),
                })
            return result, {
                "schema_version": "rvbbit.business-semantic.family-consolidation-receipt.v1",
                "generation_receipts": receipts,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001 - model/contract repair boundary
            error = f"{type(exc).__name__}: {exc}"[:4000]
            if progress_callback:
                progress_callback({
                    "event": "consolidation_failed",
                    "attempt": attempt,
                    "attempt_limit": attempts,
                    "error_class": type(exc).__name__,
                    "error": error,
                })
    raise ContractError(f"family consolidation failed after {attempts} attempts: {error}")


def _prompt(packet: Mapping[str, Any]) -> str:
    return f"""TASK: Discover the small business value system represented by this bounded database domain.

The packet already contains an evidence-backed semantic model: its parent business domain,
concepts, processes, overlapping source memberships, exact/semantic relationships, useful
questions, and known gaps. Treat that model as the meaning layer. Your job here is only to add
executable analytical projections; do not collapse the topology back into a table inventory.

The packet may also contain `prior_semantic_evidence`. Reuse exact complete dimension dictionaries
when choosing filters and categorical components. Never infer an exhaustive taxonomy from evidence
marked `bounded_sample`, `coverage_complete=false`, `member_list_complete=false`, or
`member_values_withheld=true`. Preserve rare all-time members even when they are zero in the latest
comparison period. If the evidence is marked `requires_review`, revise the existing definition or
state the conflict; do not create a duplicate metric or Value System merely because values changed.
Accepted `family_relationships` constrain identity and comparison semantics. Proposed relationships
remain hypotheses and rejected relationships must not be treated as established consolidation.

Select only one to four consequential metrics that a business
person might use to understand outcomes, their components, or their drivers. Prefer a primary
outcome such as revenue, cost, volume, retention, throughput, or service performance when the
metadata supports it. Counts are real metrics: for event sources, row count by event time and useful
low-cardinality dimensions is the defensible baseline. For current-state or catalog sources, use a
current snapshot row count observed at statement time when no event clock exists. Return no metrics
only when even that baseline would misrepresent the source.

Every metric must contain canonical read-only PostgreSQL in `event_sql`. The query must return
exactly these named columns:

  occurred_at timestamptz, value numeric, dimensions jsonb

One output row represents the stated business grain. Use only exact relations and columns from the
packet. Exclude ingestion metadata. Build dimensions with jsonb_build_object and list the same keys
in `dimensions`. Opaque IDs are not business dimensions: when a shared key supports a label join,
use product/category/channel/promotion/region names; otherwise omit the ID. Avoid entity-level
dimensions with huge cardinality unless the entity itself is the business question. The packet's
`semantic_role`, cardinality, and join opportunities are evidence for this choice.

Do not sum prices, unit costs, percentages, rates, or scores and call the result an amount. They are
non-additive. A cost amount requires a defensible quantity-times-unit-cost calculation; profit or
margin requires a defensible sales-minus-cost calculation. If the necessary join/grain is uncertain,
omit the metric and state the ambiguity. Every edge must describe a relationship embodied by the
event SQL, not a generic business truism. This is a value SYSTEM, not three unrelated KPIs: when
metrics exist, choose exactly one primary outcome; classify the others as components or drivers and
connect each to that outcome. Keep joins narrow and defensible from exact shared keys. Never
fabricate currency conversion, financial recognition rules, or missing business events.

This v1 change/contribution graph is additive by construction. Omit averages, ratios, percentages,
durations, scores, and binary indicators; they require numerator/denominator or weighting semantics
that this event-sum contract does not yet represent honestly.

Return one JSON object with this exact shape:
{{
  "name": "business-readable domain name",
  "summary": "what this domain means and how it creates or measures value",
  "primary_metric_key": "metric_key or null",
  "metrics": [{{
    "metric_key": "lower_snake_case",
    "name": "business-readable metric",
    "description": "what it means and why it matters",
    "metric_kind": "outcome" | "component" | "driver",
    "unit": "currency" | "count" | "quantity",
    "grain": "one concise business grain",
    "time_semantics": "what occurred_at means",
    "formula": "business-readable calculation using source column names",
    "event_sql": "one read-only SELECT or WITH query",
    "dimensions": [{{"key":"lower_snake_case","name":"business-readable name"}}],
    "primary_dimension_key": "one dimension key or null",
    "confidence": 0.0
  }}],
  "edges": [{{
    "parent_metric_key": "metric key",
    "child_metric_key": "different metric key",
    "relationship": "adds" | "subtracts" | "multiplies" | "divides" | "drives" | "contributes_to",
    "confidence": 0.0,
    "rationale": "business meaning of the relationship"
  }}],
  "open_questions": ["material ambiguity that a human should resolve"]
}}

DOMAIN PACKET:
{json.dumps(packet, sort_keys=True, separators=(',', ':'), ensure_ascii=False)}"""


def _finite_score(value: Any, path: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ContractError(f"{path} must be a finite number")
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise ContractError(f"{path} must be between zero and one")
    return parsed


def normalize_value_graph(packet: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    nested = payload.get("result")
    source = dict(nested) if isinstance(nested, Mapping) and "metrics" not in payload else dict(payload)
    metrics = source.get("metrics", [])
    edges = source.get("edges", [])
    questions = source.get("open_questions", [])
    semantic = packet.get("semantic_model")
    semantic = dict(semantic) if isinstance(semantic, Mapping) else {}
    graph = {
        "schema_version": VALUE_GRAPH_SCHEMA_VERSION,
        "domain_key": str(packet["domain_key"]),
        "name": " ".join(str(
            semantic.get("value_system_name")
            or source.get("name")
            or _title(str(packet["domain_key"]))
        ).split()),
        "summary": " ".join(str(
            semantic.get("value_system_summary") or source.get("summary") or ""
        ).split()),
        "primary_metric_key": source.get("primary_metric_key"),
        "source_relations": sorted(str(row["relation"]) for row in packet.get("relations", [])),
        "metrics": metrics if isinstance(metrics, list) else [],
        "edges": edges if isinstance(edges, list) else [],
        "open_questions": questions if isinstance(questions, list) else [],
        "business_domain": dict(semantic.get("business_domain") or {}),
        "value_system_key": str(semantic.get("value_system_key") or packet["domain_key"]),
        "relationship_mode": str(semantic.get("relationship_mode") or "standalone"),
        "concepts": list(semantic.get("concepts") or []),
        "processes": list(semantic.get("processes") or []),
        "source_memberships": list(semantic.get("source_memberships") or []),
        "semantic_relationships": list(semantic.get("semantic_relationships") or []),
        "analytical_questions": list(semantic.get("analytical_questions") or []),
        "gaps": list(semantic.get("gaps") or []),
    }
    primary = graph["primary_metric_key"]
    for edge in graph["edges"]:
        if (
            isinstance(edge, dict)
            and edge.get("child_metric_key") == primary
            and edge.get("parent_metric_key") != primary
        ):
            edge["parent_metric_key"], edge["child_metric_key"] = (
                edge["child_metric_key"],
                edge["parent_metric_key"],
            )
    for metric in graph["metrics"]:
        if isinstance(metric, dict) and isinstance(metric.get("event_sql"), str):
            metric["event_sql"] = metric["event_sql"].strip().removesuffix(";").strip()
    return graph


def _sanitize_identifier_dimensions(
    packet: Mapping[str, Any],
    graph: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Replace opaque IDs with joined business labels when evidence permits.

    IDs are useful join evidence, but presenting them as business hierarchy
    members is the exact mechanical failure this layer exists to avoid. Exact
    shared keys and bounded label candidates let us wrap the model's event SQL
    in fanout-safe label joins. IDs with no defensible label are omitted.
    """

    removed: list[str] = []
    enriched: list[str] = []
    opportunities = {
        str(row.get("shared_key") or ""): row
        for row in packet.get("join_opportunities", [])
        if isinstance(row, Mapping)
    }
    relation_columns = {
        str(relation.get("relation")): {
            str(column.get("name")): column
            for column in relation.get("columns", [])
            if isinstance(column, Mapping)
        }
        for relation in packet.get("relations", [])
        if isinstance(relation, Mapping)
    }
    for metric in graph.get("metrics", []):
        if not isinstance(metric, dict):
            continue
        dimensions = metric.get("dimensions")
        if not isinstance(dimensions, list):
            continue
        kept = [
            dimension
            for dimension in dimensions
            if isinstance(dimension, Mapping)
            and not str(dimension.get("key") or "").endswith("_id")
            and not re.search(r"\b(id|identifier)\b", str(dimension.get("name") or ""), re.I)
        ]
        removed_keys = {
            str(dimension.get("key") or "")
            for dimension in dimensions
            if isinstance(dimension, Mapping) and dimension not in kept
        }
        if not removed_keys:
            continue
        raw_sql = str(metric.get("event_sql") or "")
        referenced_relations = {
            relation_name
            for relation_name in relation_columns
            if re.search(rf"\b{re.escape(relation_name)}\b", raw_sql, re.I)
        }
        supported_identifiers = {
            identifier
            for identifier in removed_keys
            if any(identifier in relation_columns.get(relation_name, {}) for relation_name in referenced_relations)
        }
        for identifier in removed_keys - supported_identifiers:
            quoted_key = re.escape(identifier)
            raw_sql = re.sub(
                rf",\s*'{quoted_key}'\s*,\s*(?:[a-z_][a-z0-9_]*\.)?\"?{quoted_key}\"?(?:\s*::[a-z0-9_ ]+)?",
                "",
                raw_sql,
                flags=re.I,
            )
            raw_sql = re.sub(
                rf"'{quoted_key}'\s*,\s*(?:[a-z_][a-z0-9_]*\.)?\"?{quoted_key}\"?(?:\s*::[a-z0-9_ ]+)?\s*,\s*",
                "",
                raw_sql,
                flags=re.I,
            )
        metric["event_sql"] = raw_sql
        joins: list[str] = []
        label_expressions: list[tuple[str, str]] = []
        used_dimension_keys = {str(row.get("key")) for row in kept}
        identifier_order = sorted(
            removed_keys,
            key=lambda key: (
                0 if re.search(r"prod|item|sku", key, re.I) else
                1 if re.search(r"channel|region|country", key, re.I) else
                2 if re.search(r"promo|campaign", key, re.I) else
                3 if re.search(r"cust|customer|account", key, re.I) else 4,
                key,
            ),
        )
        for identifier_key in identifier_order:
            if len(kept) >= 6:
                break
            if identifier_key not in supported_identifiers:
                continue
            opportunity = opportunities.get(identifier_key)
            participants = opportunity.get("participants", []) if isinstance(opportunity, Mapping) else []
            candidates = [
                participant
                for participant in participants
                if isinstance(participant, Mapping)
                and participant.get("business_label_columns")
                and str(participant.get("relation") or "") not in str(metric.get("event_sql") or "")
            ]
            if not candidates:
                continue
            participant = min(
                candidates,
                key=lambda row: (int(row.get("estimated_rows") or 0), str(row.get("relation") or "")),
            )
            schema, relation = _relation_parts(str(participant["relation"]))
            column_evidence = relation_columns.get(str(participant["relation"]), {})
            estimated_rows = max(int(participant.get("estimated_rows") or 0), 1)
            def label_cardinality(value: str) -> float:
                raw = column_evidence.get(value, {}).get("distinct_estimate")
                if raw is None:
                    return float("inf")
                parsed = abs(float(raw))
                return parsed * estimated_rows if float(raw) < 0 else parsed
            labels = sorted(
                (
                    str(value)
                    for value in participant.get("business_label_columns", [])
                    if label_cardinality(str(value)) <= max(100, estimated_rows * 0.01)
                ),
                key=lambda value: (
                    0 if re.search(r"category|segment|channel|region|country|status|type", value, re.I) else 1,
                    0 if re.search(r"name|description|desc|label|title", value, re.I) else 1,
                    label_cardinality(value),
                    value,
                ),
            )[: min(2, 6 - len(kept))]
            if not labels:
                continue
            alias = f"label_{len(joins)}"
            select_labels: list[str] = []
            for label in labels:
                dimension_key = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:80]
                if not _KEY_RE.fullmatch(dimension_key) or dimension_key in used_dimension_keys:
                    dimension_key = re.sub(r"[^a-z0-9]+", "_", f"{relation}_{label}".lower()).strip("_")[:80]
                if not _KEY_RE.fullmatch(dimension_key) or dimension_key in used_dimension_keys:
                    continue
                used_dimension_keys.add(dimension_key)
                select_labels.append(
                    f"min({_quote_identifier(label)}::text) AS {_quote_identifier(dimension_key)}"
                )
                label_expressions.append(
                    (dimension_key, f"{alias}.{_quote_identifier(dimension_key)}")
                )
                kept.append({"key": dimension_key, "name": _title(label)})
                enriched.append(f"{metric.get('metric_key')}:{identifier_key}->{dimension_key}")
            if select_labels:
                joins.append(
                    f"LEFT JOIN (SELECT regexp_replace({_quote_identifier(identifier_key)}::text, '\\.0+$', '') AS join_key, "
                    f"{', '.join(select_labels)} FROM {_relation_sql(schema, relation)} "
                    f"GROUP BY regexp_replace({_quote_identifier(identifier_key)}::text, '\\.0+$', '')) {alias} "
                    f"ON {alias}.join_key=regexp_replace(source_events.dimensions::jsonb->>{_sql_literal(identifier_key)}, '\\.0+$', '')"
                )
        metric["dimensions"] = kept
        if metric.get("primary_dimension_key") in removed_keys:
            metric["primary_dimension_key"] = kept[0].get("key") if kept else None
        pairs = [
            f"{_sql_literal(str(dimension['key']))}, dimensions::jsonb->>{_sql_literal(str(dimension['key']))}"
            for dimension in kept
            if str(dimension["key"]) not in {key for key, _ in label_expressions}
        ]
        pairs.extend(
            f"{_sql_literal(key)}, {expression}"
            for key, expression in label_expressions
        )
        metric["event_sql"] = (
            "SELECT occurred_at::timestamptz AS occurred_at, value::numeric AS value, "
            + (f"jsonb_build_object({', '.join(pairs)})" if pairs else "'{}'::jsonb")
            + f" AS dimensions FROM ({metric['event_sql']}) AS source_events "
            + " ".join(joins)
        )
        removed.extend(f"{metric.get('metric_key')}:{key}" for key in sorted(removed_keys))
    return removed, enriched


def validate_value_graph(graph: Mapping[str, Any]) -> None:
    if graph.get("schema_version") != VALUE_GRAPH_SCHEMA_VERSION:
        raise ContractError("unsupported business value graph schema")
    if not isinstance(graph.get("name"), str) or not graph["name"].strip():
        raise ContractError("value graph name is required")
    if not isinstance(graph.get("summary"), str):
        raise ContractError("value graph summary must be text")
    business_domain = graph.get("business_domain", {})
    if business_domain and (
        not isinstance(business_domain, Mapping)
        or not _KEY_RE.fullmatch(str(business_domain.get("domain_key") or ""))
        or not str(business_domain.get("name") or "").strip()
    ):
        raise ContractError("value graph business_domain is invalid")
    source_relations = {
        str(value) for value in graph.get("source_relations", []) if str(value)
    }
    memberships = graph.get("source_memberships", [])
    if not isinstance(memberships, list):
        raise ContractError("value graph source_memberships must be an array")
    membership_relations = {
        str(row.get("relation"))
        for row in memberships
        if isinstance(row, Mapping) and row.get("relation")
    }
    if memberships and membership_relations != source_relations:
        raise ContractError("value graph source memberships must cover its source relations")
    for field in (
        "concepts", "processes", "semantic_relationships", "analytical_questions", "gaps"
    ):
        if not isinstance(graph.get(field, []), list):
            raise ContractError(f"value graph {field} must be an array")
    metrics = graph.get("metrics")
    if not isinstance(metrics, list) or len(metrics) > 4:
        raise ContractError("value graph metrics must be an array with at most four items")
    keys: set[str] = set()
    for index, metric in enumerate(metrics):
        if not isinstance(metric, Mapping):
            raise ContractError(f"metric {index} must be an object")
        key = metric.get("metric_key")
        if not isinstance(key, str) or not _KEY_RE.fullmatch(key) or key in keys:
            raise ContractError(f"metric {index} has an invalid or duplicate metric_key")
        keys.add(key)
        if metric.get("metric_kind") not in _METRIC_KINDS:
            raise ContractError(f"metric {key} has an invalid metric_kind")
        if metric.get("unit") not in _UNITS:
            raise ContractError(f"metric {key} has an invalid unit")
        for field in ("name", "description", "grain", "time_semantics", "formula", "event_sql"):
            if not isinstance(metric.get(field), str) or not metric[field].strip():
                raise ContractError(f"metric {key}.{field} is required")
        sql = str(metric["event_sql"])
        if ";" in sql or not re.match(r"^\s*(select|with)\b", sql, re.I):
            raise ContractError(f"metric {key}.event_sql must be one SELECT or WITH statement")
        dimensions = metric.get("dimensions")
        if not isinstance(dimensions, list) or len(dimensions) > 12:
            raise ContractError(f"metric {key}.dimensions must be a bounded array")
        dimension_keys: set[str] = set()
        for dimension in dimensions:
            if not isinstance(dimension, Mapping):
                raise ContractError(f"metric {key} dimension must be an object")
            dimension_key = dimension.get("key")
            if not isinstance(dimension_key, str) or not _KEY_RE.fullmatch(dimension_key) or dimension_key in dimension_keys:
                raise ContractError(f"metric {key} has an invalid or duplicate dimension key")
            if not isinstance(dimension.get("name"), str) or not dimension["name"].strip():
                raise ContractError(f"metric {key} dimension {dimension_key} needs a name")
            if dimension_key.endswith("_id") or re.search(r"\b(id|identifier)\b", str(dimension["name"]), re.I):
                raise ContractError(
                    f"metric {key} dimension {dimension_key} is an opaque identifier; join a business label or omit it"
                )
            dimension_keys.add(dimension_key)
        primary_dimension = metric.get("primary_dimension_key")
        if primary_dimension is not None and primary_dimension not in dimension_keys:
            raise ContractError(f"metric {key} primary_dimension_key is not declared")
        _finite_score(metric.get("confidence"), f"metric {key}.confidence")
    primary = graph.get("primary_metric_key")
    if primary is not None and primary not in keys:
        raise ContractError("primary_metric_key is not a declared metric")
    if metrics:
        outcome_keys = {
            str(metric["metric_key"])
            for metric in metrics
            if metric.get("metric_kind") == "outcome"
        }
        if outcome_keys != {primary}:
            raise ContractError("a value system must have exactly one primary outcome metric")
    edges = graph.get("edges")
    if not isinstance(edges, list) or len(edges) > 12:
        raise ContractError("value graph edges must be a bounded array")
    edge_keys: set[tuple[str, str, str]] = set()
    for index, edge in enumerate(edges):
        if not isinstance(edge, Mapping):
            raise ContractError(f"value graph edge {index} must be an object")
        parent = edge.get("parent_metric_key")
        child = edge.get("child_metric_key")
        relationship = edge.get("relationship")
        edge_key = (str(parent), str(relationship), str(child))
        if parent not in keys or child not in keys or parent == child or relationship not in _RELATIONSHIPS or edge_key in edge_keys:
            raise ContractError(f"value graph edge {index} is invalid")
        edge_keys.add(edge_key)
        _finite_score(edge.get("confidence"), f"value graph edge {index}.confidence")
    if len(metrics) > 1:
        connected = {
            str(edge["child_metric_key"])
            for edge in edges
            if edge.get("parent_metric_key") == primary
        }
        missing = keys - {str(primary)} - connected
        if missing:
            raise ContractError(
                "every component or driver must connect directly to the primary outcome: "
                + ", ".join(sorted(missing))
            )


def _bucket(minimum: datetime | None, maximum: datetime | None) -> str:
    if minimum is None or maximum is None:
        return "month"
    days = max((maximum - minimum).days, 0)
    if days >= 730:
        return "year"
    if days >= 120:
        return "month"
    if days >= 35:
        return "week"
    return "day"


def _float(value: Any) -> float:
    parsed = float(value or 0)
    return parsed if math.isfinite(parsed) else 0.0


def evaluate_metric(conn: Any, metric: Mapping[str, Any]) -> dict[str, Any]:
    """Execute and reconcile one canonical metric event relation."""

    sql = str(metric["event_sql"])
    key = str(metric["metric_key"])
    conn.execute("SET LOCAL statement_timeout='60s'")
    summary = conn.execute(
        f"""
        WITH metric_events AS ({sql})
        SELECT count(*)::bigint AS event_rows,
               count(value)::bigint AS valued_rows,
               min(occurred_at::timestamptz) AS minimum_time,
               max(occurred_at::timestamptz) AS maximum_time,
               coalesce(sum(value::numeric),0)::numeric AS total_value,
               coalesce(
                   bool_and(dimensions IS NULL OR jsonb_typeof(dimensions::jsonb)='object'),
                   true
               ) AS dimensions_valid
          FROM metric_events
        """
    ).fetchone()
    # Check emptiness before shape: bool_and() over zero rows is NULL, which
    # used to surface as a misleading "object-shaped dimensions" failure when
    # the real problem was an event projection that matched no rows.
    if summary is None or int(summary[0] or 0) == 0 or int(summary[1] or 0) == 0:
        raise ContractError(
            f"metric {key} returned no value-bearing events "
            "(the event SQL matched zero rows with a non-null time and value)"
        )
    if summary[5] is not True:
        raise ContractError(f"metric {key} did not return object-shaped dimensions")
    bucket = _bucket(summary[2], summary[3])
    series_rows = conn.execute(
        f"""
        WITH metric_events AS ({sql})
        SELECT date_trunc(%s,occurred_at::timestamptz) AS period_start,
               sum(value::numeric)::numeric AS value
          FROM metric_events
         WHERE occurred_at IS NOT NULL AND value IS NOT NULL
         GROUP BY 1 ORDER BY 1
        """,
        (bucket,),
    ).fetchall()
    series = [
        {"period_start": row[0].isoformat(), "value": _float(row[1])}
        for row in series_rows[-36:]
    ]
    comparison_series = list(series)
    anomalous_periods: list[dict[str, Any]] = []
    while len(comparison_series) >= 2:
        newest = datetime.fromisoformat(comparison_series[-1]["period_start"])
        previous = datetime.fromisoformat(comparison_series[-2]["period_start"])
        if (newest - previous).days <= 366 * 5:
            break
        anomalous_periods.insert(0, comparison_series.pop())
    prior = comparison_series[-2] if len(comparison_series) >= 2 else None
    current = comparison_series[-1] if comparison_series else None
    changes: dict[str, list[dict[str, Any]]] = {}
    reconciliations: dict[str, dict[str, Any]] = {}
    dimension_keys = [str(dimension["key"]) for dimension in metric.get("dimensions", [])]
    if prior and current and dimension_keys:
        rows = conn.execute(
            f"""
            WITH metric_events AS ({sql}), grouped AS (
              SELECT expanded.key AS dimension_key,
                     coalesce(nullif(expanded.value,''),'Unknown') AS dimension_value,
                     sum(events.value::numeric) FILTER (
                       WHERE date_trunc(%s,events.occurred_at::timestamptz)=%s::timestamptz
                     )::numeric AS prior_value,
                     sum(events.value::numeric) FILTER (
                       WHERE date_trunc(%s,events.occurred_at::timestamptz)=%s::timestamptz
                     )::numeric AS current_value
                FROM metric_events events
               CROSS JOIN LATERAL jsonb_each_text(events.dimensions::jsonb) expanded
               WHERE events.occurred_at IS NOT NULL AND events.value IS NOT NULL
                 AND expanded.key=ANY(%s)
                 AND date_trunc(%s,events.occurred_at::timestamptz) IN (%s::timestamptz,%s::timestamptz)
               GROUP BY 1,2
            )
            SELECT dimension_key,dimension_value,
                   coalesce(prior_value,0),coalesce(current_value,0)
              FROM grouped
            """,
            (
                bucket,
                prior["period_start"],
                bucket,
                current["period_start"],
                dimension_keys,
                bucket,
                prior["period_start"],
                current["period_start"],
            ),
        ).fetchall()
        grouped_rows: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            grouped_rows[str(row[0])].append(row)
        for dimension_key in dimension_keys:
            all_changes = [
                {
                    "dimension_value": str(row[1]),
                    "prior_value": _float(row[2]),
                    "current_value": _float(row[3]),
                    "change": _float(row[3]) - _float(row[2]),
                }
                for row in grouped_rows.get(dimension_key, [])
            ]
            total_prior = sum(item["prior_value"] for item in all_changes)
            total_current = sum(item["current_value"] for item in all_changes)
            total_change = total_current - total_prior
            for item in all_changes:
                item["share_of_change"] = item["change"] / total_change if total_change else 0.0
            all_changes.sort(key=lambda item: (-abs(item["change"]), item["dimension_value"]))
            changes[dimension_key] = all_changes[:12]
            expected_prior = float(prior["value"])
            expected_current = float(current["value"])
            tolerance = max(abs(expected_prior), abs(expected_current), 1.0) * 1e-8
            reconciliations[dimension_key] = {
                "prior_difference": total_prior - expected_prior,
                "current_difference": total_current - expected_current,
                "passed": abs(total_prior - expected_prior) <= tolerance and abs(total_current - expected_current) <= tolerance,
            }
    return {
        "metric_key": key,
        "event_rows": int(summary[0]),
        "valued_rows": int(summary[1]),
        "minimum_time": summary[2].isoformat() if summary[2] else None,
        "maximum_time": summary[3].isoformat() if summary[3] else None,
        "total_value": _float(summary[4]),
        "bucket": bucket,
        "series": series,
        "anomalous_periods_excluded_from_change": anomalous_periods,
        "prior_period": prior,
        "current_period": current,
        "total_change": (float(current["value"]) - float(prior["value"])) if prior and current else None,
        "changes": changes,
        "reconciliations": reconciliations,
        "cube_recommendation": {
            "metric": key,
            "time_dimension": "occurred_at",
            "time_grain": bucket,
            "dimensions": [str(row["key"]) for row in metric.get("dimensions", [])],
            "source": "validated_metric_event_projection",
        },
    }


def evaluate_value_graph(conn: Any, graph: Mapping[str, Any]) -> dict[str, Any]:
    validate_value_graph(graph)
    evaluations = [evaluate_metric(conn, metric) for metric in graph.get("metrics", [])]
    return {
        "schema_version": "rvbbit.business-value.evaluation.v1",
        "worker_version": VALUE_GRAPH_WORKER_VERSION,
        "metrics": evaluations,
        "validated_metrics": len(evaluations),
        "all_dimension_reconciliations_passed": all(
            reconciliation.get("passed") is True
            for evaluation in evaluations
            for reconciliation in evaluation.get("reconciliations", {}).values()
        ),
    }


def _salvage_executable_metrics(
    conn: Any,
    graph: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, str]]]:
    """Keep a valid primary outcome when optional components fail execution."""

    original_metric_labels = {
        str(metric.get("metric_key") or ""): {
            str(metric.get("metric_key") or "").lower(),
            str(metric.get("metric_key") or "").lower().replace("_", " "),
            str(metric.get("name") or "").lower(),
        }
        for metric in graph.get("metrics", [])
        if isinstance(metric, Mapping)
    }
    primary = str(graph.get("primary_metric_key") or "")
    direct_children = {
        str(edge.get("child_metric_key"))
        for edge in graph.get("edges", [])
        if isinstance(edge, Mapping) and str(edge.get("parent_metric_key")) == primary
    }
    graph["metrics"] = [
        metric for metric in graph.get("metrics", [])
        if str(metric.get("metric_key")) == primary
        or (
            str(metric.get("metric_key")) in direct_children
            and metric.get("metric_kind") in {"component", "driver"}
            and metric.get("unit") in _UNITS
        )
    ]
    candidate_keys = {str(metric.get("metric_key")) for metric in graph["metrics"]}
    graph["edges"] = [
        edge for edge in graph.get("edges", [])
        if str(edge.get("parent_metric_key")) == primary
        and str(edge.get("child_metric_key")) in candidate_keys
    ]
    validate_value_graph(graph)
    evaluations: list[dict[str, Any]] = []
    metric_errors: list[dict[str, str]] = []
    surviving_keys: set[str] = set()
    for metric in graph.get("metrics", []):
        try:
            evaluations.append(evaluate_metric(conn, metric))
            surviving_keys.add(str(metric["metric_key"]))
        except Exception as exc:  # noqa: BLE001 - bounded SQL execution boundary
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()
            metric_errors.append(
                {
                    "metric_key": str(metric.get("metric_key") or ""),
                    "error": f"{type(exc).__name__}: {exc}"[:2000],
                }
            )
    if primary not in surviving_keys:
        raise ContractError("the primary outcome metric did not execute")
    graph["metrics"] = [
        metric for metric in graph.get("metrics", [])
        if str(metric.get("metric_key")) in surviving_keys
    ]
    graph["edges"] = [
        edge for edge in graph.get("edges", [])
        if str(edge.get("parent_metric_key")) in surviving_keys
        and str(edge.get("child_metric_key")) in surviving_keys
    ]
    dropped_labels = {
        label
        for metric_key, labels in original_metric_labels.items()
        if metric_key not in surviving_keys
        for label in labels
        if len(label) >= 5
    }
    graph["open_questions"] = [
        question
        for question in graph.get("open_questions", [])
        if not any(label in str(question).lower() for label in dropped_labels)
    ]
    validate_value_graph(graph)
    return graph, {
        "schema_version": "rvbbit.business-value.evaluation.v1",
        "worker_version": VALUE_GRAPH_WORKER_VERSION,
        "metrics": evaluations,
        "validated_metrics": len(evaluations),
        "all_dimension_reconciliations_passed": all(
            reconciliation.get("passed") is True
            for evaluation in evaluations
            for reconciliation in evaluation.get("reconciliations", {}).values()
        ),
    }, metric_errors


def _fallback_stream_executes(conn: Any, relation: str, time_column: str, measure: str) -> bool:
    """Cheaply confirm one candidate event stream yields at least one event."""
    schema, name = _relation_parts(relation)
    try:
        conn.execute("SET LOCAL statement_timeout='15s'")
        row = conn.execute(
            f"SELECT 1 FROM {_relation_sql(schema, name)} "
            f"WHERE {_quote_identifier(time_column)} IS NOT NULL "
            f"AND {_quote_identifier(measure)} IS NOT NULL LIMIT 1"
        ).fetchone()
        return row is not None
    except Exception:  # noqa: BLE001 - a candidate that cannot be probed is not usable
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        return False


def _select_fallback_stream(
    candidates: list[Mapping[str, Any]], conn: Any | None
) -> tuple[Mapping[str, Any], str, str] | None:
    """Pick (candidate, measure, time_column) for the deterministic fallback.

    Without a connection the first candidate's first columns are used, exactly
    as before. With one, candidates and their time columns are probed in
    priority order and the first stream that actually yields an event wins;
    a relation whose only time column is entirely NULL (a common shape for
    audit or image timestamps) can otherwise make the fallback fail
    deterministically every time the model path also fails.
    """
    for candidate in candidates:
        measures = [str(column) for column in candidate.get("measure_columns", [])]
        times = [str(column) for column in candidate.get("time_columns", [])]
        if not measures or not times:
            continue
        measure = measures[0]
        if conn is None:
            return candidate, measure, times[0]
        for time_column in times:
            if _fallback_stream_executes(conn, str(candidate["relation"]), time_column, measure):
                return candidate, measure, time_column
    return None


def _estimated_distinct(column: Mapping[str, Any], estimated_rows: int) -> float | None:
    value = column.get("distinct_estimate")
    if value is None:
        return None
    parsed = float(value)
    return abs(parsed) * estimated_rows if parsed < 0 else parsed


def _baseline_count_graph(
    packet: Mapping[str, Any],
    graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Guarantee one honest count metric for an otherwise metric-free domain.

    Event-like relations use their best timestamp.  Current-state relations use
    PostgreSQL's statement timestamp, making the metric explicitly an as-of
    observation rather than pretending a modification timestamp is an event.
    """

    relations = [
        relation
        for relation in packet.get("relations", [])
        if isinstance(relation, Mapping) and relation.get("relation")
    ]
    if not relations:
        return dict(graph or {})
    fact_relations = [
        str(candidate.get("relation"))
        for candidate in packet.get("fact_candidates", [])
        if isinstance(candidate, Mapping) and candidate.get("relation")
    ]
    relation = next(
        (item for name in fact_relations for item in relations if item["relation"] == name),
        max(relations, key=lambda item: int(item.get("estimated_rows") or 0)),
    )
    qualified = str(relation["relation"])
    schema, relation_name = _relation_parts(qualified)
    estimated_rows = int(relation.get("estimated_rows") or 0)
    columns = [
        column for column in relation.get("columns", []) if isinstance(column, Mapping)
    ]
    time_column = next(
        (
            str(candidate.get("time_columns", [])[0])
            for candidate in packet.get("fact_candidates", [])
            if isinstance(candidate, Mapping)
            and str(candidate.get("relation")) == qualified
            and candidate.get("time_columns")
        ),
        None,
    )
    if time_column and re.search(r"(^|_)(created|modified|updated|loaded|ingested|sync)(_|$)", time_column, re.I):
        time_column = None
    dimension_columns: list[str] = []
    for column in columns:
        name = str(column.get("name") or "")
        if (
            not _KEY_RE.fullmatch(name.lower())
            or _ID_NAME_RE.search(name)
            or column.get("semantic_role") not in {"business_label", "categorical_attribute"}
        ):
            continue
        distinct = _estimated_distinct(column, estimated_rows)
        if distinct is not None and not 1 < distinct <= 100:
            continue
        dimension_columns.append(name)
        if len(dimension_columns) >= 4:
            break
    dimensions = [
        {"key": column.lower(), "name": _title(column)} for column in dimension_columns
    ]
    pairs = [
        value
        for column in dimension_columns
        for value in (_sql_literal(column.lower()), f"{_quote_identifier(column)}::text")
    ]
    dimensions_sql = (
        f"jsonb_build_object({','.join(pairs)})" if pairs else "'{}'::jsonb"
    )
    occurred_at = (
        f"{_quote_identifier(time_column)}::timestamptz"
        if time_column
        else "statement_timestamp()"
    )
    where = f" WHERE {_quote_identifier(time_column)} IS NOT NULL" if time_column else ""
    metric_key = re.sub(r"[^a-z0-9]+", "_", f"{relation_name}_row_count".lower()).strip("_")[:80]
    metric = {
        "metric_key": metric_key,
        "name": f"{_title(relation_name)} Row Count",
        "description": (
            f"Count of {_title(relation_name).lower()} rows, split by stable business dimensions."
        ),
        "metric_kind": "outcome",
        "unit": "count",
        "grain": f"one {relation_name} row",
        "time_semantics": (
            f"event time recorded by {time_column}"
            if time_column
            else "current-state observation time at query execution"
        ),
        "formula": "count(*) represented as sum(1)",
        "event_sql": (
            f"SELECT {occurred_at} AS occurred_at, 1::numeric AS value, "
            f"{dimensions_sql} AS dimensions FROM {_relation_sql(schema, relation_name)}{where}"
        ),
        "dimensions": dimensions,
        "primary_dimension_key": dimensions[0]["key"] if dimensions else None,
        "confidence": 0.9 if time_column else 0.82,
    }
    source = dict(graph or {})
    source.update(
        {
            "name": source.get("name") or _title(str(packet["domain_key"])),
            "summary": source.get("summary")
            or f"A countable business population represented by {_title(relation_name)}.",
            "primary_metric_key": metric_key,
            "metrics": [metric],
            "edges": [],
            "open_questions": list(source.get("open_questions") or []),
        }
    )
    return normalize_value_graph(packet, source)


def _fallback_graph(packet: Mapping[str, Any], conn: Any | None = None) -> dict[str, Any]:
    candidates = list(packet.get("fact_candidates", []))
    selected = _select_fallback_stream(candidates, conn)
    if selected is None:
        return _baseline_count_graph(
            packet,
            normalize_value_graph(
                packet,
                {
                    "name": _title(str(packet["domain_key"])),
                    "summary": "No richer value stream was found; the domain retains its baseline row-count metric.",
                    "primary_metric_key": None,
                    "metrics": [],
                    "edges": [],
                    "open_questions": ["Which richer business outcome should this domain be accountable for?"],
                },
            ),
        )
    candidate, measure, time_column = selected
    schema, relation = _relation_parts(str(candidate["relation"]))
    # The deterministic fallback cannot safely guess which relation supplies a
    # human label, so it deliberately stays atomic instead of relabeling raw
    # identifiers as if they were business dimensions.
    dimension_columns: list[str] = []
    dimension_pairs: list[str] = []
    dimensions: list[dict[str, str]] = []
    for column in dimension_columns:
        key = re.sub(r"_id$", "", column.lower()) or column.lower()
        if not _KEY_RE.fullmatch(key) or any(row["key"] == key for row in dimensions):
            continue
        dimension_pairs.extend([_sql_literal(key), f"{_quote_identifier(column)}::text"])
        dimensions.append({"key": key, "name": _title(key)})
    dimensions_sql = (
        f"jsonb_build_object({','.join(dimension_pairs)})"
        if dimension_pairs else "'{}'::jsonb"
    )
    metric_key = re.sub(r"[^a-z0-9]+", "_", measure.lower()).strip("_")[:80]
    metric = {
        "metric_key": metric_key,
        "name": _title(measure),
        "description": f"Sum of {_title(measure).lower()} over the {_title(relation).lower()} event stream.",
        "metric_kind": "outcome",
        "unit": "currency" if re.search(r"amount|revenue|sales|price|cost", measure, re.I) else "quantity",
        "grain": f"one {relation} event row",
        "time_semantics": f"event time recorded by {time_column}",
        "formula": f"sum({_title(measure)})",
        "event_sql": (
            f"SELECT {_quote_identifier(time_column)}::timestamptz AS occurred_at, "
            f"{_quote_identifier(measure)}::numeric AS value, {dimensions_sql} AS dimensions "
            f"FROM {_relation_sql(schema, relation)} "
            f"WHERE {_quote_identifier(time_column)} IS NOT NULL AND {_quote_identifier(measure)} IS NOT NULL"
        ),
        "dimensions": dimensions,
        "primary_dimension_key": dimensions[0]["key"] if dimensions else None,
        "confidence": 0.55,
    }
    return normalize_value_graph(
        packet,
        {
            "name": f"{_title(schema)} Value Stream",
            "summary": f"A validated value stream centered on {_title(measure).lower()} from {_title(relation)}.",
            "primary_metric_key": metric_key,
            "metrics": [metric],
            "edges": [],
            "open_questions": ["Confirm the business definition and unit before governing this metric."],
        },
    )


def discover_value_graph(
    conn: Any,
    packet: Mapping[str, Any],
    client: Any,
    *,
    repair_attempts: int = 1,
    progress_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Generate, execute, and reconcile one domain graph with a safe fallback."""

    prompt = _prompt(packet)
    receipts: list[dict[str, Any]] = []
    error: str | None = None
    previous: str | None = None
    last_graph: dict[str, Any] | None = None
    last_stripped_dimensions: list[str] = []
    last_enriched_dimensions: list[str] = []
    for attempt in range(repair_attempts + 1):
        user = prompt
        if error is not None:
            user += f"\n\nCORRECTION REQUIRED:\n{error}\n\nPRIOR JSON:\n{(previous or '')[:24000]}"
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "inference_started",
                    "attempt": attempt + 1,
                    "attempt_limit": repair_attempts + 1,
                }
            )
        try:
            content, raw_receipt = client.complete_json(
                system=(
                    "You are a business metric architect. Prefer a few executable, reconciled business "
                    "definitions over exhaustive schema commentary. Return JSON only."
                ),
                user=user,
                max_tokens=8_000,
            )
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:4000]
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "inference_failed",
                        "attempt": attempt + 1,
                        "attempt_limit": repair_attempts + 1,
                        "error_class": type(exc).__name__,
                        "error": error,
                    }
                )
            # Transport/read timeouts are not semantic evidence. Give the same
            # strong-model lane its bounded retry before considering the
            # deterministic fallback; never silently reroute judgment to the
            # small structural-assembly model.
            continue
        receipt = dict(raw_receipt)
        receipt["attempt"] = attempt + 1
        receipt["value_graph_contract_version"] = VALUE_GRAPH_SCHEMA_VERSION
        receipts.append(receipt)
        if progress_callback is not None:
            progress_callback(
                {
                    "event": "inference_completed",
                    "attempt": attempt + 1,
                    "attempt_limit": repair_attempts + 1,
                    "latency_ms": receipt.get("latency_ms"),
                    "model_version": receipt.get("model_version"),
                }
            )
        previous = content
        try:
            graph = normalize_value_graph(packet, _json_object(content))
            baseline_metric_added = not bool(graph.get("metrics"))
            if baseline_metric_added:
                graph = _baseline_count_graph(packet, graph)
            stripped_dimensions, enriched_dimensions = _sanitize_identifier_dimensions(packet, graph)
            last_graph = graph
            last_stripped_dimensions = stripped_dimensions
            last_enriched_dimensions = enriched_dimensions
            evaluation = evaluate_value_graph(conn, graph)
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "validation_completed",
                        "attempt": attempt + 1,
                        "attempt_limit": repair_attempts + 1,
                    }
                )
            return graph, {
                "schema_version": "rvbbit.business-value.receipt.v1",
                "worker_version": VALUE_GRAPH_WORKER_VERSION,
                "generation_receipts": receipts,
                "evaluation": evaluation,
                "stripped_identifier_dimensions": stripped_dimensions,
                "enriched_identifier_dimensions": enriched_dimensions,
                "baseline_metric_added": baseline_metric_added,
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001 - model and database repair boundary
            # Database execution errors are useful repair feedback too.  The
            # final fallback is deterministic and is evaluated by the same path.
            error = f"{type(exc).__name__}: {exc}"[:4000]
            receipt["validation_error"] = error
            if progress_callback is not None:
                progress_callback(
                    {
                        "event": "validation_failed",
                        "attempt": attempt + 1,
                        "attempt_limit": repair_attempts + 1,
                        "error_class": type(exc).__name__,
                        "error": error,
                    }
                )
            # A rejected event projection can leave PostgreSQL's transaction
            # aborted.  Clear that failed read-only transaction before the
            # repair attempt (or deterministic fallback) executes.  Connection
            # attributes such as ``read_only`` survive the rollback.
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()

    if last_graph is not None:
        try:
            graph, evaluation, metric_errors = _salvage_executable_metrics(conn, last_graph)
            return graph, {
                "schema_version": "rvbbit.business-value.receipt.v1",
                "worker_version": VALUE_GRAPH_WORKER_VERSION,
                "generation_receipts": receipts,
                "evaluation": evaluation,
                "stripped_identifier_dimensions": last_stripped_dimensions,
                "enriched_identifier_dimensions": last_enriched_dimensions,
                "metric_errors": metric_errors,
                "partial": bool(metric_errors),
                "fallback": False,
            }
        except Exception as exc:  # noqa: BLE001 - deterministic fallback boundary
            error = f"{type(exc).__name__}: {exc}"[:4000]
            rollback = getattr(conn, "rollback", None)
            if callable(rollback):
                rollback()

    fallback = _fallback_graph(packet, conn)
    try:
        evaluation = evaluate_value_graph(conn, fallback)
    except Exception as exc:  # noqa: BLE001 - keep both failure causes in the receipt
        rollback = getattr(conn, "rollback", None)
        if callable(rollback):
            rollback()
        raise ContractError(
            f"deterministic fallback value stream failed ({type(exc).__name__}: {exc}); "
            f"last model attempt failed with: {error}"
        ) from exc
    return fallback, {
        "schema_version": "rvbbit.business-value.receipt.v1",
        "worker_version": VALUE_GRAPH_WORKER_VERSION,
        "generation_receipts": receipts,
        "evaluation": evaluation,
        "fallback": True,
        "fallback_reason": error,
    }


def group_relations_by_schema(relations: Sequence[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    for relation in relations:
        schema, _ = _relation_parts(str(relation))
        grouped[schema].append(str(relation))
    return {schema: sorted(values) for schema, values in sorted(grouped.items())}
