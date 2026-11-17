"""Meaning narrative worker (docs/MEANING_PLAN.md Tier 1 + Tier 2, migration 0440).

Recognition runs for new or structurally changed sources. Maps add new sources
without replacing existing subjects. Dossiers refresh only affected subjects;
reviewed publications remain visible throughout. Each narrative model attempt
reserves budget and appends its receipt independently of materialized outputs.

The model is told whose schema it is looking at and answers JSON only, with
no tools and no memory (a direct chat completion through the registered
``openrouter`` backend, the same lane the excavator's judgment used). Every
expected join is probed against real values before it counts; every domain
is reviewed by Jev (makes sense? kind?) before it is marked usable. A daily
budget caps spend; the queue lives in SQL (``rvbbit.meaning_narrative_queue``)
so a restart loses nothing.
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

DEFAULT_MODEL = "google/gemini-3.8-flash"
DEFAULT_BACKEND = "openrouter"
TICK_SECONDS = 45
RECOGNITION_MAX_TOKENS = 7000
DOMAIN_MAX_TOKENS = 3500
# Rough OpenRouter list prices for the default model, USD per million tokens;
# receipts mark the figure as an estimate. Override per deployment.
PRICE_IN_PER_M = float(os.environ.get("WAREHOUSE_MEANING_NARRATIVE_PRICE_IN_USD_PER_M", "0.30") or 0.30)
PRICE_OUT_PER_M = float(os.environ.get("WAREHOUSE_MEANING_NARRATIVE_PRICE_OUT_USD_PER_M", "2.50") or 2.50)

_RECOGNITION_SYSTEM = (
    "You recognize a data source from its schema for a business analyst. You are told the source system "
    "and shown its tables (row mass, keys, time columns, sample column names, category values, verified "
    "references). Name the product or system these tables come from, say what each table is called by the "
    "people who use it, what one row is, its role, and any third-party package it belongs to. List the "
    "joins the vendor's object model implies between the tables shown, as from-table, column, to-table "
    "(to-column when it is not id). Give the standard questions people ask of this source. Prefer the "
    "vendor's own vocabulary; keep custom objects' business names short; never invent tables or columns "
    "that are not in the input. Return exactly one JSON object and nothing else."
)
_RECOGNITION_CONTRACT = {
    "product": "string: the SaaS product or system (e.g. 'Salesforce Sales Cloud', 'Canvas LMS', 'Google Ads')",
    "confidence": "number 0..1",
    "objects": [{
        "table": "string: the table name exactly as given",
        "business_name": "string: what people call it (e.g. 'Lead', 'Opportunity', 'SMS Conversation')",
        "role": "one of: entity | event | junction | lookup | config | package | history",
        "package": "string or null: third-party package / namespace (e.g. 'SMS Magic', 'NBAVS')",
        "one_line": "string: one plain sentence saying what one row is",
    }],
    "expected_joins": [{"from": "table", "column": "column on from", "to": "table", "to_column": "column on to, usually id"}],
    "standard_questions": ["string: questions people ask of this source, at most 8"],
    "ignore": ["table names that are setup, metadata or bookkeeping rather than business data"],
}

from semantic_contracts import normalize_idea


_IDEAS_SYSTEM = (
    " A candidate metric proposes ONE scalar value that is useful WITHOUT caller arguments and can be saved daily. "
    "Define the business population, formula, units/denominator, actual time column or snapshot basis, "
    "and default window (with boundaries/time zone). Daily history is what was observed then, not a "
    "recalculation of old dates from today's data. A rolling metric such as sightings in the last 30 days "
    "is valid when that window is explicitly justified and part of its identity. Never invent a selected "
    "date range, assume 30 days because a date column exists, or enumerate dimensional parameter combinations. "
    "Use literal defaults when helpful. Counts are useful when they measure a meaningful business population; "
    "avoid generic table row counts. Missing semantics must remain an incomplete proposal, not a canonical metric. "
    "Reuse registered measures/cubes. Put grouped grids, rankings, temporal comparisons and filtered views in "
    "candidate_breakdowns: a named analysis with purpose, measures, dimensions, grain and caveats. "
    "A cube is a reusable fact population at a declared row grain, not one chart, date range or dimensional cut. "
    "Shared dimensions do not prove the same fact population or safe record-level joins. "
    "Metrics and breakdowns are proposals, not approved definitions. "
)
_METRIC_IDEA = {"name": "string", "formula": "scalar aggregate using real columns, no grouping",
                "grain": "one scalar at the explicitly defined default scope", "why": "business purpose",
                "population": "included business records and fixed filters",
                "unit": "unit and denominator if relevant",
                "time_basis": "actual date column, current snapshot basis, or all recorded data",
                "window": "explicit default window and boundaries; no unspecified selection",
                "time_zone": "IANA time zone when temporal", "defaults": {}}
_BREAKDOWN_IDEA = {"name": "string: named comparison", "purpose": "string: what this helps explain and why",
                   "measures": ["string: calculation naming real columns"],
                   "dimensions": ["string: real column or published label dimension"],
                   "grain": "string: what one result row represents", "caveats": "string: interpretation limits"}

_DOMAIN_SYSTEM = (
    "You describe one business subject area for an analyst who has never seen these tables. You are given "
    "the source system, the tables in this cluster (their names, what one row is, keys, time columns, "
    "category values, measures, and the verified joins between them). Write for a business reader: a short "
    "name in the form '<Source> · <Subject>' (e.g. 'Salesforce · Lead pipeline'), two to four sentences on "
    "what this area records and what it is used for, the questions people ask of it, and a few candidate "
    "metrics with a plain formula naming real columns (no SQL). If the tables are configuration, plumbing, "
    "or too mixed to be one subject, say so in the name and keep the description honest. Never invent "
    "columns." + _IDEAS_SYSTEM + "Return exactly one JSON object and nothing else."
)
_DOMAIN_CONTRACT = {
    "name": "string: '<Source> · <Subject>'",
    "about": "string: 2-4 sentences",
    "questions": ["string, at most 6"],
    "candidate_metrics": [_METRIC_IDEA],
    "candidate_breakdowns": [_BREAKDOWN_IDEA],
    "concerns": ["string: gaps, ambiguities, things a person should confirm; at most 4"],
}

_MAP_SYSTEM = (
    "You are mapping one company's data estate into the business subjects its people would recognize. You are "
    "given every recognized source system with its product, the questions people ask of it, and its named "
    "objects (what one row is, row mass, what each joins to), plus the joins that cross sources and, in "
    "`shared_labels`, classifications that already carry the same dimension across several tables — two "
    "tables sharing one belong near each other even when no column joins them. Produce the "
    "company's subjects: as few as honestly cover the business (typically 6 to 12, never more than 15), "
    "cross-source wherever the same subject spans systems (a lead pipeline lives in the CRM, the marketing "
    "marts and the ad platforms at once). Name subjects the way an executive would ('Admissions & lead "
    "pipeline', 'Paid media', 'Tuition & payments', 'Course operations'), never after a table or a vendor. "
    "For each subject list only its CORE objects, the 3 to 12 tables the subject is really about, as "
    "schema.table exactly as given; every other table will be assigned to a subject afterwards by a "
    "classifier that sees your subject descriptions, so make the descriptions specific enough to place "
    "breakdowns, snapshots, history, configuration and packages' setup objects correctly (they belong to the "
    "subject they serve; never make a subject out of them). Keep the whole reply under 4000 words. Return "
    "exactly one JSON object and nothing else. Existing subjects in `existing_subjects` are durable: "
    "reuse their exact keys for additions, never rename, replace or move their tables. Only place "
    "unassigned tables. Create a new subject only when none of the existing subjects fits. "
)
_MAP_CONTRACT = {
    "company": "string: what this company does, one sentence",
    "subjects": [{
        "key": "string: short snake_case key",
        "name": "string: executive name",
        "about": "string: 2-3 sentences on what this subject records and who uses it",
        "questions": ["string: the questions the company asks of it, at most 6"],
        "members": [{"table": "schema.table", "role": "core"}],
        "also_covers": "string: one sentence naming the kinds of supporting, history, configuration and package tables that belong here",
    }],
}

_DOSSIER_SYSTEM = (
    "You are writing the dossier for one business subject at one company, for an analyst who has never seen "
    "these tables. You are given the subject's name and first description, and every table placed in it "
    "(what one row is, kind, row mass, keys, time columns, category values, measures, verified joins, and the "
    "role a first pass gave it). Write: a fuller description (4-6 sentences: what the subject records, how the "
    "systems feed each other, who uses it), the sub-areas inside it with their tables, the core spine (the "
    "tables the subject is really about, in join order), a role for every table (core | support | configuration "
    "| plumbing), the questions people ask, candidate metrics with a plain formula naming real columns, and the "
    "gaps or ambiguities a person should confirm. Keep configuration and plumbing tables in the subject with "
    "that role; never invent columns. Give roles only for tables whose role should differ from role_from_map "
    "or that have none; do not restate the rest. "
    "`label_dimensions` are classifications Meaning already ran over these tables' text, published as real "
    "dimensions you can group by; `shared_labels` are the ones that exist on more than one table. Treat them "
    "as first-class: when a shared dimension lets two tables be compared that no column joins, say so in an "
    "area or a question, and prefer named breakdowns that group by a label dimension over ones that need a "
    "column nobody has. "
    "`stated` is what people stated about member tables: dated caveats, and columns to ignore. A stated fact "
    "outranks anything the profile or the numbers suggest: never propose a join, an area or a candidate metric a "
    "caveat rules out, never build a metric on an ignored column, and when a caveat says two tables are "
    "disjoint or unrelated, keep them apart. "
    + _IDEAS_SYSTEM + "Keep the whole reply under 2500 words. Return exactly one "
    "JSON object and nothing else."
)
_DOSSIER_CONTRACT = {
    "about": "string: 4-6 sentences",
    "areas": [{"name": "string", "about": "string: one sentence", "tables": ["schema.table"]}],
    "core": ["schema.table in join order"],
    "roles": {"schema.table": "core | support | configuration | plumbing"},
    "questions": ["string, at most 8"],
    "candidate_metrics": [_METRIC_IDEA],
    "candidate_breakdowns": [_BREAKDOWN_IDEA],
    "gaps": ["string, at most 5"],
}
_ENRICH_SYSTEM = (
    "You are proposing classifications for one business subject. You are given its dossier, its tables with "
    "candidate text and code columns and sampled values from each, the classifications that already exist, "
    "label dimensions other tables already share, and the SQL functions available to you (`capabilities`). "
    "Propose classifications that would let an analyst group, filter or compare these tables in a way the raw "
    "columns do not allow. Prefer ones that could connect tables that do not join: the same label dimension on "
    "two tables is how this subject learns they are about the same thing. When a dimension in `existing` or "
    "`shared_labels` already asks the question you want to ask of another table, propose it again for that "
    "table under EXACTLY the same `name` and the same criteria: reusing the name is what makes the two "
    "tables comparable, and a near-duplicate under a new name bridges nothing. Each proposal names ONE table, one to "
    "four of its listed columns, and either a choice question (2-16 labels, each with a one-line criterion, plus "
    "instructions), a truth question, or a decision tree built from the listed builders when one question cannot "
    "settle it. Ground every criterion in the sampled values you were shown: never invent categories the values "
    "do not support, never name a column that is not listed, never classify something the column already states "
    "literally. Say what connection each one is expected to enable. If nothing here is worth classifying, return "
    "an empty proposals list and say why in `skipped`. "
    "`retired` lists classifications that were tried on these tables and withdrawn, with the reason: never "
    "propose one of those names for that table again, and do not propose the same question under a new name. "
    "`stated` is what people stated about the tables (caveats, ignored columns); a caveat outranks anything the "
    "samples suggest, so do not propose a classification a caveat rules out or one over an ignored column. "
    "Return exactly one JSON object and nothing else."
)
_ENRICH_CONTRACT = {
    "proposals": [{
        "relation": "schema.table",
        "name": "slug_for_the_dimension",
        "label_name": "Human name for the dimension",
        "columns": ["column names from that table's candidate list"],
        "question": {
            "kind": "choice | truth | tree",
            "instructions": "what Jev is deciding, in one or two sentences",
            "criteria": {"label_slug": "one line saying what belongs in this label"},
            "threshold": "number 0-1, truth only",
            "tree": "tree only: a node built from leaf/branch/choice_branch",
        },
        "why": "one sentence",
        "expects": "the connection or comparison this makes possible",
    }],
    "skipped": "string, optional: why nothing was proposed",
}

MAP_MAX_TOKENS = 20000
DOSSIER_MAX_TOKENS = 16000
ENRICH_MAX_TOKENS = 6000
ENRICH_MAX_PROPOSALS = 4
ENRICH_SAMPLE_N = 500
ENRICH_MODEL = "calliope-sol"

_worker_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _log(message: str) -> None:
    print(f"meaning narrative: {message}", file=sys.stderr, flush=True)


def extract_json_object(content: str) -> dict[str, Any]:
    """First complete JSON object in a model reply (models wrap in fences or prose)."""
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except ValueError:
        pass
    start = text.find("{")
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)) if start >= 0 else []:
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                value = json.loads(text[start:index + 1])
                if isinstance(value, dict):
                    return value
                break
    raise ValueError("model reply did not contain one complete JSON object")


def normalize_recognition(output: Mapping[str, Any], known_tables: set[str]) -> dict[str, Any]:
    """Keep only shapes the SQL side applies; drop objects and joins that name tables not in the packet."""
    roles = {"entity", "event", "junction", "lookup", "config", "package", "history"}
    objects = []
    for item in output.get("objects") or []:
        if not isinstance(item, Mapping):
            continue
        table = str(item.get("table") or item.get("relation") or "").strip().lower()
        table = table.split(".")[-1]
        if table not in known_tables:
            continue
        role = str(item.get("role") or "").strip().lower()
        objects.append({
            "table": table,
            "business_name": str(item.get("business_name") or "").strip()[:120] or None,
            "role": role if role in roles else None,
            "package": (str(item.get("package") or "").strip()[:80] or None),
            "one_line": str(item.get("one_line") or "").strip()[:400] or None,
        })
    joins = []
    for item in output.get("expected_joins") or []:
        if not isinstance(item, Mapping):
            continue
        source = str(item.get("from") or "").strip().lower().split(".")[-1]
        target = str(item.get("to") or "").strip().lower().split(".")[-1]
        column = str(item.get("column") or item.get("from_column") or "").strip().lower()
        if source in known_tables and target in known_tables and column and len(joins) < 60:
            joins.append({"from": source, "column": column, "to": target,
                          "to_column": (str(item.get("to_column") or "id").strip().lower() or "id")})
    questions = [str(q).strip()[:200] for q in (output.get("standard_questions") or []) if str(q).strip()][:8]
    ignore = [str(t).strip().lower().split(".")[-1] for t in (output.get("ignore") or []) if str(t).strip()][:60]
    confidence = output.get("confidence")
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = None
    return {
        "product": str(output.get("product") or "").strip()[:120] or None,
        "confidence": confidence,
        "objects": objects,
        "expected_joins": joins,
        "standard_questions": questions,
        "ignore": [t for t in ignore if t in known_tables],
    }


def normalize_breakdowns(output: Mapping[str, Any]) -> list[dict[str, Any]]:
    """A breakdown needs a purpose, computations and dimensions, not just a title."""
    result = []
    for item in output.get("candidate_breakdowns") or []:
        if not isinstance(item, Mapping):
            continue
        clean = {k: str(item.get(k) or "").strip()[:600] for k in ("name", "purpose", "grain", "caveats")}
        for key in ("measures", "dimensions"):
            values = item.get(key)
            clean[key] = list(dict.fromkeys(v.strip()[:400] for v in values
                             if isinstance(v, str) and v.strip()))[:8] if isinstance(values, list) else []
        if all(clean[k] for k in ("name", "purpose", "measures", "dimensions")):
            result.append(clean)
        if len(result) >= 10:
            break
    return result


def normalize_domain(output: Mapping[str, Any]) -> dict[str, Any]:
    metrics = []
    for item in output.get("candidate_metrics") or []:
        if not isinstance(item, Mapping) or not str(item.get("name") or "").strip():
            continue
        metrics.append(normalize_idea(item))
        if len(metrics) >= 8:
            break
    return {
        "name": str(output.get("name") or "").strip()[:120] or None,
        "about": str(output.get("about") or "").strip()[:1200] or None,
        "questions": [str(q).strip()[:200] for q in (output.get("questions") or []) if str(q).strip()][:6],
        "candidate_metrics": metrics,
        "candidate_breakdowns": normalize_breakdowns(output),
        "concerns": [str(c).strip()[:300] for c in (output.get("concerns") or []) if str(c).strip()][:4],
    }


def normalize_map(output: Mapping[str, Any], known: set[str]) -> dict[str, Any]:
    """Subjects with members restricted to known schema.table names; at most 15 subjects."""
    roles = {"core", "support", "configuration", "plumbing"}
    subjects = []
    seen_tables: set[str] = set()
    for item in output.get("subjects") or []:
        if not isinstance(item, Mapping) or not str(item.get("name") or "").strip():
            continue
        members = []
        for member in item.get("members") or []:
            if isinstance(member, Mapping):
                table = str(member.get("table") or "").strip().lower()
                role = str(member.get("role") or "core").strip().lower()
            else:
                table, role = str(member or "").strip().lower(), "core"
            if table not in known or table in seen_tables:
                continue
            seen_tables.add(table)
            members.append({"table": table, "role": role if role in roles else "core"})
        about = str(item.get("about") or "").strip()
        covers = str(item.get("also_covers") or "").strip()
        subjects.append({
            "key": re.sub(r"[^a-z0-9]+", "_", str(item.get("key") or item.get("name")).strip().lower()).strip("_")[:60],
            "name": str(item.get("name")).strip()[:120],
            "about": ((about + (" Also covers: " + covers if covers else "")).strip()[:1200]) or None,
            "questions": [str(q).strip()[:200] for q in (item.get("questions") or []) if str(q).strip()][:6],
            "members": members,
        })
        if len(subjects) >= 15:
            break
    return {
        "company": str(output.get("company") or "").strip()[:300] or None,
        "subjects": subjects,
        "unplaced": [str(t).strip().lower() for t in (output.get("unplaced") or [])
                     if str(t).strip().lower() in known and str(t).strip().lower() not in seen_tables][:400],
    }


def normalize_dossier(output: Mapping[str, Any], members: set[str]) -> dict[str, Any]:
    roles_ok = {"core", "support", "configuration", "plumbing"}
    roles = {}
    raw_roles = output.get("roles") if isinstance(output.get("roles"), Mapping) else {}
    for table, role in raw_roles.items():
        t = str(table).strip().lower()
        r = str(role or "").strip().lower()
        if t in members and r in roles_ok:
            roles[t] = r
    core = [str(t).strip().lower() for t in (output.get("core") or []) if str(t).strip().lower() in members][:40]
    for t in core:
        roles.setdefault(t, "core")
    areas = []
    for area in output.get("areas") or []:
        if not isinstance(area, Mapping) or not str(area.get("name") or "").strip():
            continue
        tables = [str(t).strip().lower() for t in (area.get("tables") or []) if str(t).strip().lower() in members]
        areas.append({"name": str(area.get("name")).strip()[:120], "about": str(area.get("about") or "").strip()[:300], "tables": tables[:60]})
        if len(areas) >= 12:
            break
    metrics = []
    for item in output.get("candidate_metrics") or []:
        if isinstance(item, Mapping) and str(item.get("name") or "").strip():
            metrics.append(normalize_idea(item))
        if len(metrics) >= 10:
            break
    return {
        "about": str(output.get("about") or "").strip()[:1600] or None,
        "areas": areas,
        "core": core,
        "roles": roles,
        "questions": [str(q).strip()[:200] for q in (output.get("questions") or []) if str(q).strip()][:8],
        "candidate_metrics": metrics,
        "candidate_breakdowns": normalize_breakdowns(output),
        "gaps": [str(g).strip()[:300] for g in (output.get("gaps") or []) if str(g).strip()][:5],
    }


def normalize_enrich(output: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any]:
    """Keep proposals that name a table and columns the packet actually offered.

    The model chooses what to classify; it never chooses what to read. A
    proposal naming a column we did not show it is a hallucination, not a
    request, so it is dropped here rather than rejected later by SQL.
    """
    offered: dict[str, set[str]] = {}
    for table in (packet.get("tables") or []):
        if not isinstance(table, Mapping):
            continue
        name = str(table.get("table") or "").strip().lower()
        if not name:
            continue
        offered[name] = {
            str((c or {}).get("column") or "").strip().lower()
            for c in (table.get("columns") or [])
            if isinstance(c, Mapping)
        }
    # names a person (or the disuse pruner) withdrew on a table stay withdrawn: SQL would refuse them
    # (meaning_enrich_register → rejected=retired_label, 0457); dropping them here saves the round trip
    retired: set[tuple[str, str]] = {
        (str(r.get("table") or "").strip().lower(), str(r.get("name") or "").strip().lower())
        for r in (packet.get("retired") or []) if isinstance(r, Mapping)
    }
    kept: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in (output.get("proposals") or []):
        if not isinstance(item, Mapping) or len(kept) >= ENRICH_MAX_PROPOSALS:
            continue
        relation = str(item.get("relation") or "").strip().lower()
        if relation not in offered:
            continue
        columns = [
            str(c).strip() for c in (item.get("columns") or [])
            if str(c).strip().lower() in offered[relation]
        ]
        if not columns:
            continue
        question = item.get("question") if isinstance(item.get("question"), Mapping) else {}
        kind = str(question.get("kind") or "").strip().lower()
        if kind not in {"choice", "truth", "tree"}:
            continue
        criteria = {
            str(k).strip(): str(v).strip()
            for k, v in (question.get("criteria") or {}).items()
            if str(k).strip() and str(v).strip()
        } if isinstance(question.get("criteria"), Mapping) else {}
        if kind == "choice" and len(criteria) < 2:
            continue
        if kind == "tree" and not isinstance(question.get("tree"), Mapping):
            continue
        name = re.sub(r"[^a-z0-9]+", "_", str(item.get("name") or "").strip().lower()).strip("_")[:40]
        if len(name) < 2 or (relation, name) in seen:
            continue
        if (relation, name) in retired:
            _log(f"enrich: {relation}.{name} was retired on this table; proposal dropped")
            continue
        seen.add((relation, name))
        shaped: dict[str, Any] = {"kind": kind, "instructions": str(question.get("instructions") or "").strip()}
        if criteria:
            shaped["criteria"] = criteria
        if kind == "truth":
            try:
                shaped["threshold"] = min(max(float(question.get("threshold") or 0.5), 0.05), 0.95)
            except (TypeError, ValueError):
                shaped["threshold"] = 0.5
        if kind == "tree":
            shaped["tree"] = question["tree"]
        kept.append({
            "relation": relation,
            "name": name,
            "label_name": str(item.get("label_name") or name.replace("_", " ").title()).strip()[:80],
            "columns": columns[:4],
            "question": shaped,
            "why": str(item.get("why") or "").strip()[:400],
            "expects": str(item.get("expects") or "").strip()[:400],
        })
    return {"proposals": kept, "skipped": str(output.get("skipped") or "").strip()[:400]}


def _usage(receipt: Mapping[str, Any]) -> tuple[int, int, float]:
    usage = receipt.get("usage") if isinstance(receipt.get("usage"), Mapping) else {}
    tokens_in = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    cost = usage.get("cost")
    try:
        cost = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost = None
    if cost is None:
        cost = tokens_in / 1e6 * PRICE_IN_PER_M + tokens_out / 1e6 * PRICE_OUT_PER_M
    return tokens_in, tokens_out, round(cost, 6)


class BudgetExceeded(RuntimeError):
    """No provider request was made because the next reservation exceeds budget."""


class AccountedClient:
    """Record every attempt, including malformed JSON and unknown transport outcomes."""
    def __init__(self, client, conn_factory, budget, kind, key, reason):
        self.client, self.conn_factory, self.budget = client, conn_factory, budget
        self.kind, self.key, self.reason = kind, key, reason
        self.model = getattr(client, "model", "")
        self.attempt = 0

    def complete_json(self, *, system, user, max_tokens):
        # Conservatively estimate input at one token per UTF-8 byte, plus output
        # capacity, with a configurable minimum. Actual provider receipts settle
        # the reservation; unknown outcomes retain it instead of appearing free.
        reserve = max(float(os.environ.get("WAREHOUSE_MEANING_CALL_RESERVE_USD", "0.25")),
                      (len((system + user).encode()) * PRICE_IN_PER_M
                       + max_tokens * PRICE_OUT_PER_M) / 1e6 * 2)
        self.attempt += 1
        with self.conn_factory() as conn:
            call_id = conn.execute(
                "SELECT rvbbit.meaning_reserve_model_call(%s,%s,%s,%s,%s::numeric,%s::numeric) AS id",
                (self.kind, self.key, f"{self.reason}; attempt {self.attempt}", self.model,
                 self.budget, reserve)).fetchone()["id"]
        if call_id is None:
            raise BudgetExceeded("Daily model budget has insufficient room for the next call")
        try:
            content, receipt = self.client.complete_json(system=system, user=user, max_tokens=max_tokens)
        except Exception:
            self._record(call_id, "failed", None, None, reserve)
            raise
        tokens_in, tokens_out, cost = _usage(receipt)
        usage = receipt.get("usage") or {}
        if not usage or (usage.get("cost") is None and not (tokens_in or tokens_out)):
            cost = reserve
        self._record(call_id, "charged", tokens_in, tokens_out, cost)
        import cost_accounting
        cost_accounting.record_completion(
            self.conn_factory, f"meaning:{call_id}", "Meaning", f"meaning.{self.kind}",
            self.client, receipt, work_key=self.key)
        return content, receipt

    def _record(self, call_id, event, tokens_in, tokens_out, cost):
        with self.conn_factory() as conn:
            conn.execute(
                "INSERT INTO rvbbit.meaning_model_calls(call_id,event,kind,work_key,reason,model,tokens_in,tokens_out,cost_usd) "
                "SELECT call_id,%s,kind,work_key,reason,model,%s,%s,%s FROM rvbbit.meaning_model_calls "
                "WHERE call_id=%s AND event='reserved'",
                (event, tokens_in, tokens_out, cost, call_id))


def _complete(client: Any, *, system: str, user: str, max_tokens: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """One completion plus one 'JSON only' repair when the reply is not a JSON object."""
    content, receipt = client.complete_json(system=system, user=user, max_tokens=max_tokens)
    try:
        return extract_json_object(content), dict(receipt)
    except ValueError:
        first_head = str(content or "")[:200].replace("\n", " ")
        content, receipt2 = client.complete_json(
            system=system, user=user + "\n\nReturn exactly one JSON object and nothing else.", max_tokens=max_tokens)
        merged = dict(receipt2)
        merged["repair"] = True
        first_in, first_out, first_cost = _usage(receipt)
        _, _, second_cost = _usage(receipt2)
        usage = dict(merged.get("usage") or {})
        usage["prompt_tokens"] = int(usage.get("prompt_tokens") or 0) + first_in
        usage["completion_tokens"] = int(usage.get("completion_tokens") or 0) + first_out
        usage["cost"] = first_cost + second_cost
        merged["usage"] = usage
        try:
            return extract_json_object(content), merged
        except ValueError as exc:
            tail = str(content or "")[-160:].replace("\n", " ")
            raise ValueError(f"{exc} (reply {len(str(content or ''))} chars; first try began {first_head!r}; retry ended {tail!r})") from exc


def run_recognition(conn_factory: Callable[[], Any], client: Any, schema: str) -> dict[str, Any]:
    with conn_factory() as conn:
        packet = conn.execute("SELECT rvbbit.meaning_recognition_packet(%s::text) AS p", (schema,)).fetchone()["p"]
        fingerprint = conn.execute("SELECT rvbbit.meaning_recognition_fingerprint(%s::text) AS f", (schema,)).fetchone()["f"]
    if isinstance(packet, str):
        packet = json.loads(packet)
    known = {str(item.get("table")).lower() for item in (packet.get("relations") or [])}
    user = ("SOURCE PACKET:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_RECOGNITION_CONTRACT, ensure_ascii=False))
    try:
        raw, receipt = _complete(client, system=_RECOGNITION_SYSTEM, user=user, max_tokens=RECOGNITION_MAX_TOKENS)
        output = normalize_recognition(raw, known)
        tokens_in, tokens_out, cost = _usage(receipt)
        with conn_factory() as conn:
            verification = conn.execute("SELECT rvbbit.meaning_apply_recognition(%s::text, %s::jsonb) AS v",
                                        (schema, json.dumps(output))).fetchone()["v"]
            conn.execute(
                "INSERT INTO rvbbit.meaning_recognitions AS r (schema_name, source_system, fingerprint, packet, output, verification, model, tokens_in, tokens_out, cost_usd, status, error, attempts, updated_at) "
                "VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, %s, 'done', NULL, 1, now()) "
                "ON CONFLICT (schema_name) DO UPDATE SET source_system = EXCLUDED.source_system, fingerprint = EXCLUDED.fingerprint, packet = EXCLUDED.packet, "
                "output = EXCLUDED.output, verification = EXCLUDED.verification, model = EXCLUDED.model, tokens_in = EXCLUDED.tokens_in, "
                "tokens_out = EXCLUDED.tokens_out, cost_usd = r.cost_usd + EXCLUDED.cost_usd, status = 'done', error = NULL, attempts = r.attempts + 1, updated_at = now()",
                (schema, packet.get("source_system"), fingerprint, json.dumps(packet), json.dumps(output),
                 json.dumps(verification, default=str), str(receipt.get("model_version") or receipt.get("model") or client.model),
                 tokens_in, tokens_out, cost),
            )
        _log(f"recognized {schema}: product={output.get('product')!r} objects={len(output['objects'])} "
             f"joins={verification.get('joins') if isinstance(verification, dict) else verification} cost=${cost:.4f}")
        return {"status": "done", "schema": schema, "cost_usd": cost, "objects": len(output["objects"])}
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, retried later by the queue
        message = f"{type(exc).__name__}: {exc}"[:800]
        with conn_factory() as conn:
            conn.execute(
                "INSERT INTO rvbbit.meaning_recognitions AS r (schema_name, fingerprint, status, error, attempts, updated_at) "
                "VALUES (%s, %s, 'error', %s, 1, now()) "
                "ON CONFLICT (schema_name) DO UPDATE SET status = 'error', error = EXCLUDED.error, attempts = r.attempts + 1, updated_at = now()",
                (schema, fingerprint, message),
            )
        _log(f"recognition failed for {schema}: {message}")
        return {"status": "error", "schema": schema, "error": message}


def run_domain(conn_factory: Callable[[], Any], client: Any, unit_key: str, schema: str,
               members: list[str], member_fingerprint: str) -> dict[str, Any]:
    with conn_factory() as conn:
        packet = conn.execute("SELECT rvbbit.meaning_unit_packet(%s::text, %s::text[]) AS p", (schema, members)).fetchone()["p"]
    if isinstance(packet, str):
        packet = json.loads(packet)
    user = ("CLUSTER PACKET:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_DOMAIN_CONTRACT, ensure_ascii=False))
    try:
        raw, receipt = _complete(client, system=_DOMAIN_SYSTEM, user=user, max_tokens=DOMAIN_MAX_TOKENS)
        output = normalize_domain(raw)
        if not output["name"] or not output["about"]:
            raise ValueError("domain reply lacked a name or description")
        tokens_in, tokens_out, cost = _usage(receipt)
        with conn_factory() as conn:
            conn.execute(
                # explicit casts: psycopg dumps floats as float8 and small ints as int2, and
                # function resolution has no implicit float8→numeric cast
                "SELECT rvbbit.meaning_apply_domain(%s::text, %s::text, %s::text[], %s::text, %s::jsonb, %s::jsonb, %s::text, %s::int, %s::int, %s::numeric)",
                (unit_key, schema, members, member_fingerprint, json.dumps(output), json.dumps(packet),
                 str(receipt.get("model_version") or receipt.get("model") or client.model), tokens_in, tokens_out, cost),
            )
            review = conn.execute("SELECT rvbbit.meaning_review_domain(%s::text) AS r", (unit_key,)).fetchone()["r"]
        _log(f"domain {unit_key} ({len(members)} tables): {output['name']!r} review={review} cost=${cost:.4f}")
        return {"status": "done", "domain_key": unit_key, "cost_usd": cost, "review": review}
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"[:800]
        with conn_factory() as conn:
            conn.execute(
                "INSERT INTO rvbbit.meaning_domains AS d (domain_key, schema_name, members, member_fingerprint, status, error, attempts, updated_at) "
                "VALUES (%s, %s, %s::text[], %s, 'error', %s, 1, now()) "
                "ON CONFLICT (domain_key) DO UPDATE SET status = 'error', error = EXCLUDED.error, attempts = d.attempts + 1, updated_at = now()",
                (unit_key, schema, members, member_fingerprint, message),
            )
        _log(f"domain failed for {unit_key}: {message}")
        return {"status": "error", "domain_key": unit_key, "error": message}


def assign_mapped_sources(conn: Any) -> dict[str, Any]:
    """Do not classify a new source against subjects from an older estate map.

    Recognition and mapping are separate queue ticks. A successful old map is
    not sufficient: the current source must have participated in a map first.
    The SQL tail assignment considers all unplaced tables, so defer that pass
    while any eligible source is still awaiting its first map.
    """
    waiting = conn.execute(
        "SELECT DISTINCT m.schema_name FROM rvbbit.meaning_relations m "
        "JOIN rvbbit.business_sources b ON b.schema_name=m.schema_name "
        "WHERE b.role IN ('business','derived') AND m.status='judged' "
        "AND m.domain_key IS NULL AND m.placed_by IS DISTINCT FROM 'override' "
        "AND coalesce(m.plumbing,0)<0.7 AND coalesce(m.kind,'')<>'system_plumbing' "
        "AND NOT EXISTS (SELECT 1 FROM rvbbit.meaning_map_sources s WHERE s.schema_name=m.schema_name) "
        "ORDER BY m.schema_name"
    ).fetchall()
    if waiting:
        return {"status": "awaiting_source_map", "sources": [r["schema_name"] for r in waiting]}
    return conn.execute("SELECT rvbbit.meaning_assign_unplaced(600) AS a").fetchone()["a"]


def run_map(conn_factory: Callable[[], Any], client: Any, fingerprint: str) -> dict[str, Any]:
    """One call over every recognized source: the company's subjects."""
    with conn_factory() as conn:
        packet = conn.execute("SELECT rvbbit.meaning_map_packet() AS p").fetchone()["p"]
        rows = conn.execute("SELECT relation FROM rvbbit.meaning_relations WHERE status = 'judged'").fetchall()
        map_id = conn.execute(
            "INSERT INTO rvbbit.meaning_company_map (fingerprint, packet, status) VALUES (%s::text, %s::jsonb, 'pending') RETURNING map_id",
            (fingerprint, json.dumps(packet, default=str)),
        ).fetchone()["map_id"]
    if isinstance(packet, str):
        packet = json.loads(packet)
    with conn_factory() as conn:
        packet["existing_subjects"] = conn.execute(
            "SELECT substr(domain_key,9) AS key,name,about,members FROM rvbbit.meaning_domains "
            "WHERE origin='map' AND status<>'folded' ORDER BY domain_key").fetchall()
    known = {str(r["relation"]).lower() for r in rows}
    user = ("COMPANY DATA ESTATE:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_MAP_CONTRACT, ensure_ascii=False))
    try:
        raw, receipt = _complete(client, system=_MAP_SYSTEM, user=user, max_tokens=MAP_MAX_TOKENS)
        output = normalize_map(raw, known)
        if not output["subjects"]:
            raise ValueError("map reply named no subjects")
        tokens_in, tokens_out, cost = _usage(receipt)
        with conn_factory() as conn, conn.transaction():
            conn.execute(
                "UPDATE rvbbit.meaning_company_map SET output = %s::jsonb, model = %s::text, tokens_in = %s::int, tokens_out = %s::int, "
                "cost_usd = %s::numeric, status = 'done', error = NULL WHERE map_id = %s::bigint",
                (json.dumps(output), str(receipt.get("model_version") or receipt.get("model") or client.model), tokens_in, tokens_out, cost, map_id),
            )
            applied = conn.execute("SELECT rvbbit.meaning_apply_map(%s::bigint, %s::jsonb) AS a", (map_id, json.dumps(output))).fetchone()["a"]
            assigned = assign_mapped_sources(conn)
        _log(f"map #{map_id}: {output.get('company')!r} subjects={len(output['subjects'])} applied={applied} jev_assigned={assigned} cost=${cost:.4f}")
        return {"status": "done", "map_id": map_id, "cost_usd": cost, "subjects": len(output["subjects"]), "assigned": assigned}
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"[:800]
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.meaning_company_map SET status = 'error', error = %s::text WHERE map_id = %s::bigint", (message, map_id))
        _log(f"map failed: {message}")
        return {"status": "error", "error": message}


def run_dossier(conn_factory: Callable[[], Any], client: Any, key: str) -> dict[str, Any]:
    with conn_factory() as conn:
        packet = conn.execute("SELECT rvbbit.meaning_subject_packet(%s::text) AS p", (key,)).fetchone()["p"]
        subject = conn.execute("SELECT members, rvbbit.meaning_subject_fingerprint(domain_key) AS fingerprint FROM rvbbit.meaning_domains WHERE domain_key = %s::text", (key,)).fetchone()
        if subject is None:
            _log(f"dossier {key}: subject no longer exists (re-keyed by a newer map); skipped")
            return None
        members = subject["members"]
    if isinstance(packet, str):
        packet = json.loads(packet)
    member_set = {str(m).lower() for m in (members or [])}
    user = ("SUBJECT PACKET:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"))
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_DOSSIER_CONTRACT, ensure_ascii=False))
    try:
        raw, receipt = _complete(client, system=_DOSSIER_SYSTEM, user=user, max_tokens=DOSSIER_MAX_TOKENS)
        output = normalize_dossier(raw, member_set)
        if not output["about"]:
            raise ValueError("dossier reply lacked a description")
        tokens_in, tokens_out, cost = _usage(receipt)
        with conn_factory() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(hashtext('rvbbit.meaning.publication'))")
            current = conn.execute(
                "SELECT rvbbit.meaning_subject_fingerprint(domain_key) AS fingerprint "
                "FROM rvbbit.meaning_domains WHERE domain_key=%s FOR UPDATE", (key,)).fetchone()
            if current is None or current["fingerprint"] != subject["fingerprint"]:
                return {"status": "superseded", "domain_key": key, "cost_usd": cost}
            applied = conn.execute(
                "SELECT rvbbit.meaning_apply_dossier(%s::text, %s::jsonb, %s::jsonb, %s::text, %s::int, %s::int, %s::numeric) AS a",
                (key, json.dumps(output), json.dumps(packet, default=str),
                 str(receipt.get("model_version") or receipt.get("model") or client.model), tokens_in, tokens_out, cost),
            ).fetchone()["a"]
            review = conn.execute("SELECT rvbbit.meaning_review_domain(%s::text) AS r", (key,)).fetchone()["r"]
            if not review or not review.get("usable"):
                raise ValueError("Dossier did not pass publication review")
            latest = conn.execute("SELECT rvbbit.meaning_subject_fingerprint(%s) AS fingerprint", (key,)).fetchone()
            if latest["fingerprint"] != subject["fingerprint"]:
                raise ValueError("Subject changed during publication review; draft discarded")
            conn.execute("UPDATE rvbbit.meaning_domains SET narrative_fingerprint=%s, attempts=0 WHERE domain_key=%s",
                         (subject["fingerprint"], key))
            conn.execute("SELECT rvbbit.meaning_publish_subject(%s)", (key,))
        _log(f"dossier {key}: core={applied} review={review} cost=${cost:.4f}")
        return {"status": "done", "domain_key": key, "cost_usd": cost, "review": review}
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        message = f"{type(exc).__name__}: {exc}"[:800]
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.meaning_domains SET status = 'error', error = %s::text, attempts = attempts + 1, updated_at = now() WHERE domain_key = %s::text",
                         (message, key))
        _log(f"dossier failed for {key}: {message}")
        return {"status": "error", "domain_key": key, "error": message}


def run_enrich(conn_factory: Callable[[], Any], client: Any, key: str) -> dict[str, Any]:
    """Propose, sample, judge and publish label dimensions for one subject.

    The model names a relation, its columns and a question. Every query that
    touches the data is composed in SQL; every row-level decision is Jev's.
    A proposal whose sampled labels do not split the data usefully gets one
    revision with the reason, then is dropped.
    """
    try:
        with conn_factory() as conn:
            packet = conn.execute("SELECT rvbbit.meaning_enrich_packet(%s::text) AS p", (key,)).fetchone()["p"]
    except Exception as exc:  # noqa: BLE001 - a broken packet is this subject's problem, not the tick's
        message = f"{type(exc).__name__}: {exc}"[:800]
        _log(f"enrich packet failed for {key}: {message}")
        return {"status": "error", "domain_key": key, "error": message}
    if isinstance(packet, str):
        packet = json.loads(packet)
    if not packet:
        _log(f"enrich {key}: subject no longer exists; skipped")
        return None
    if not (packet.get("tables") or []):
        with conn_factory() as conn:
            conn.execute("SELECT rvbbit.meaning_enrich_sentinel(%s::text, %s::text)", (key, "no candidate columns"))
        _log(f"enrich {key}: no table has a text or code column worth classifying")
        return {"status": "nothing_to_enrich", "domain_key": key}

    user = ("SUBJECT PACKET:\n" + json.dumps(packet, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_ENRICH_CONTRACT, ensure_ascii=False))
    try:
        raw, receipt = _complete(client, system=_ENRICH_SYSTEM, user=user, max_tokens=ENRICH_MAX_TOKENS)
        shaped = normalize_enrich(raw, packet)
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - recorded, retried later by the queue
        message = f"{type(exc).__name__}: {exc}"[:800]
        with conn_factory() as conn:
            conn.execute("SELECT rvbbit.meaning_enrich_sentinel(%s::text, %s::text)", (key, message))
            conn.execute("UPDATE rvbbit.meaning_enrichments SET status = 'error' WHERE subject_key = %s::text AND relation = '-'", (key,))
        _log(f"enrich failed for {key}: {message}")
        return {"status": "error", "domain_key": key, "error": message}

    tokens_in, tokens_out, cost = _usage(receipt)
    model = str(receipt.get("model_version") or receipt.get("model") or getattr(client, "model", "") or "")
    proposals = shaped["proposals"]
    if not proposals:
        with conn_factory() as conn:
            conn.execute("SELECT rvbbit.meaning_enrich_sentinel(%s::text, %s::text)",
                         (key, shaped.get("skipped") or "none proposed"))
        _log(f"enrich {key}: nothing proposed ({shaped.get('skipped') or 'no reason given'}); cost=${cost:.4f}")
        return {"status": "none_proposed", "domain_key": key, "cost_usd": cost}

    share = round(cost / max(len(proposals), 1), 8)
    first_pass = not (packet.get("existing") or [])
    published, dropped = 0, 0
    for proposal in proposals:
        outcome = _enrich_one(conn_factory, client, key, proposal, model, share, packet)
        if outcome.get("published"):
            published += 1
        else:
            dropped += 1
    with conn_factory() as conn:
        conn.execute("SELECT rvbbit.meaning_enrich_sentinel(%s::text, %s::text)",
                     (key, f"pass complete: {published} published, {dropped} dropped"))
        # A fresh appliance writes every dossier before a single label exists, so
        # the dossier that would cite them was written blind and nothing would
        # ever rewrite it. The subject's FIRST published dimensions send its
        # dossier back for one rewrite; the pass after that sees them in
        # `existing`, so this fires at most once per subject and cannot loop.
        if first_pass and published:
            conn.execute(
                "UPDATE rvbbit.meaning_domains SET status = 'placed', updated_at = now() "
                "WHERE domain_key = %s::text AND origin = 'map' AND status = 'done'", (key,))
            _log(f"enrich {key}: first {published} dimensions published; dossier queued for a rewrite that can cite them")
    _log(f"enrich {key}: {published} published, {dropped} dropped, proposal cost=${cost:.4f}")
    return {"status": "done", "domain_key": key, "published": published, "dropped": dropped, "cost_usd": cost}


def _enrich_one(conn_factory: Callable[[], Any], client: Any, key: str, proposal: Mapping[str, Any],
                model: str, cost_share: float, packet: Mapping[str, Any]) -> dict[str, Any]:
    """Register, sample and judge one proposal; revise once when the labels do not split the data."""
    label = f"{proposal.get('relation')}.{proposal.get('name')}"
    for attempt in (1, 2):
        with conn_factory() as conn:
            registered = conn.execute(
                "SELECT rvbbit.meaning_enrich_register(%s::text, %s::jsonb, %s::text) AS r",
                (key, json.dumps(proposal), model)).fetchone()["r"]
        if isinstance(registered, str):
            registered = json.loads(registered)
        if registered.get("rejected"):
            _log(f"enrich {key}: {label} rejected ({registered['rejected']})")
            return {"published": False, "reason": registered["rejected"]}

        enrichment_id = int(registered["id"])
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.meaning_enrichments SET cost_usd = cost_usd + %s::numeric WHERE id = %s",
                         (cost_share, enrichment_id))
            sample = conn.execute("SELECT rvbbit.meaning_enrich_sample(%s::bigint, %s::int) AS s",
                                  (enrichment_id, ENRICH_SAMPLE_N)).fetchone()["s"]
            if isinstance(sample, str):
                sample = json.loads(sample)
            if sample.get("status") == "error":
                _log(f"enrich {key}: {label} sample failed ({sample.get('error')})")
                return {"published": False, "reason": "sample_failed"}
            verdict = conn.execute("SELECT rvbbit.meaning_enrich_judge(%s::bigint) AS j", (enrichment_id,)).fetchone()["j"]
            if isinstance(verdict, str):
                verdict = json.loads(verdict)

        if verdict.get("keep"):
            with conn_factory() as conn:
                out = conn.execute("SELECT rvbbit.meaning_enrich_publish(%s::bigint) AS p", (enrichment_id,)).fetchone()["p"]
            if isinstance(out, str):
                out = json.loads(out)
            _log(f"enrich {key}: {label} published as {out.get('view')} "
                 f"({out.get('labels')} labels over {sample.get('rows')} rows, judge={verdict.get('score')})")
            return {"published": True, "view": out.get("view")}

        if attempt == 2:
            with conn_factory() as conn:
                conn.execute("SELECT rvbbit.meaning_enrich_retire(%s::bigint, %s::text)",
                             (enrichment_id, f"judge: {verdict.get('reason') or 'not useful'}"))
            _log(f"enrich {key}: {label} dropped after revision ({verdict.get('reason')})")
            return {"published": False, "reason": "judged"}

        revised = _enrich_revision(client, proposal, sample, verdict, packet)
        if revised is None:
            with conn_factory() as conn:
                conn.execute("SELECT rvbbit.meaning_enrich_retire(%s::bigint, %s::text)",
                             (enrichment_id, f"judge: {verdict.get('reason') or 'not useful'}"))
            _log(f"enrich {key}: {label} dropped, no revision ({verdict.get('reason')})")
            return {"published": False, "reason": "judged"}
        _log(f"enrich {key}: {label} revising ({verdict.get('reason')})")
        proposal = revised
    return {"published": False, "reason": "judged"}


def _enrich_revision(client: Any, proposal: Mapping[str, Any], sample: Mapping[str, Any],
                     verdict: Mapping[str, Any], packet: Mapping[str, Any]) -> dict[str, Any] | None:
    """One more attempt at criteria that actually split the data, or None to drop it."""
    table = next((t for t in (packet.get("tables") or [])
                  if str((t or {}).get("table") or "").lower() == proposal.get("relation")), {})
    user = ("YOUR PROPOSAL:\n" + json.dumps(proposal, ensure_ascii=False, separators=(",", ":"))
            + "\n\nWHAT JEV PRODUCED OVER " + str(sample.get("rows") or 0) + " SAMPLED ROWS:\n"
            + json.dumps(sample.get("distribution") or [], ensure_ascii=False)
            + "\n\nWHY IT WAS REJECTED: " + str(verdict.get("reason") or "the labels do not split the data usefully")
            + "\n\nTHE TABLE AND ITS SAMPLED VALUES:\n"
            + json.dumps(table, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\n\nOUTPUT CONTRACT (JSON):\n" + json.dumps(_ENRICH_CONTRACT, ensure_ascii=False))
    system = (
        "A classification you proposed did not split the data usefully. Either revise it so it does - different "
        "labels, sharper criteria, a different column on the same table - or give up on it. Ground the new "
        "criteria in the sampled values you are shown. Return exactly one JSON object with a `proposals` list "
        "holding at most one revised proposal for the same table, or an empty list with `skipped` explaining why "
        "this table cannot be classified usefully. Nothing else."
    )
    try:
        raw, _ = _complete(client, system=system, user=user, max_tokens=ENRICH_MAX_TOKENS)
        shaped = normalize_enrich(raw, packet)
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed revision is a dropped proposal, not a failed pass
        _log(f"enrich revision failed: {type(exc).__name__}: {exc}")
        return None
    for item in shaped["proposals"]:
        if item["relation"] == proposal.get("relation"):
            item["name"] = proposal.get("name")  # keep the slot; a revision replaces, never duplicates
            return item
    return None



LEDGER_COURT_MAX_CARDS = 10


def _ledger_grounding_session(conn: Any, owner: str) -> str | None:
    """The session a proposal card is grounded to: the owner's setup session if
    one exists, else their most recent thread. Cards are per owner; the
    observation they point at is company-scoped."""
    row = conn.execute(
        "SELECT id::text AS id FROM rvbbit.calliope_sessions "
        "WHERE lower(owner_email)=lower(%s) AND purpose='setup' LIMIT 1",
        (owner,),
    ).fetchone()
    if row:
        return str(row["id"])
    row = conn.execute(
        "SELECT id::text AS id FROM rvbbit.calliope_sessions "
        "WHERE lower(owner_email)=lower(%s) AND NOT coalesce(archived, false) "
        "ORDER BY updated_at DESC NULLS LAST LIMIT 1",
        (owner,),
    ).fetchone()
    return str(row["id"]) if row else None


def _ledger_referent_label(referent: Mapping[str, Any]) -> str:
    kind = str(referent.get("kind") or "")
    if kind == "metric":
        return f"the metric `{referent.get('name')}`"
    if kind == "relation":
        return f"the table `{referent.get('schema')}.{referent.get('relation')}`"
    if kind == "artifact":
        return f"the artifact `{referent.get('slug')}`"
    return f"{kind} `{referent.get('ref_id') or referent.get('name') or ''}`"


def run_ledger_court(conn_factory: Callable[[], Any]) -> dict[str, Any]:
    """Turn open miner proposals into decision cards, and apply answered cards.

    Plan §5b: proposals arrive in Awaiting You — the same decision machinery
    the Floor already renders — as "People say X (N times, K people, D% resolving
    to Y). Bind it?". Approve → affirmed. Set aside → declined, and the miner's
    findings ledger suppresses re-proposal. The person who said the term most
    gets the card; nobody is asked twice about the same pair.
    """
    import calliope_decisions  # standalone: needs only a connection factory

    out: dict[str, Any] = {"kind": "ledger", "cards": 0, "affirmed": 0, "declined": 0, "skipped": 0, "errors": []}
    with conn_factory() as conn:
        pending = conn.execute(
            "SELECT id::text AS id, term_norm, referent_key, referent, evidence, owner_email "
            "FROM rvbbit.calliope_term_binding_findings "
            "WHERE resolved_at IS NULL AND verdict='proposed' AND decision_id IS NULL "
            "ORDER BY (evidence->>'mentions')::int DESC NULLS LAST, created_at LIMIT %s",
            (LEDGER_COURT_MAX_CARDS,),
        ).fetchall()
    for finding in pending:
        owner = str(finding.get("owner_email") or "").strip().lower()
        if "@" not in owner:
            out["skipped"] += 1
            continue
        referent = finding.get("referent") if isinstance(finding.get("referent"), Mapping) else {}
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), Mapping) else {}
        term = str(finding.get("term_norm") or "")
        mentions = int(evidence.get("mentions") or 0)
        people = int(evidence.get("distinct_users") or 0)
        dominance = float(evidence.get("dominance") or 0)
        label = _ledger_referent_label(referent)
        question = f"People say \u201c{term}\u201d and mean {label}. Bind it?"
        summary = (
            f"\u201c{term}\u201d appeared in {mentions} turns from {people} "
            f"{'person' if people == 1 else 'people'}, and {dominance:.0%} of the time the turn resolved to {label}. "
            "Binding makes that the settled meaning: the term pins its referent in search and arrives pre-resolved "
            "in context, so nobody re-derives it. Setting aside stops the question for 90 days."
        )
        try:
            with conn_factory() as conn:
                session_id = _ledger_grounding_session(conn, owner)
                if not session_id:
                    out["skipped"] += 1
                    continue
                observation = conn.execute(
                    "INSERT INTO rvbbit.calliope_observations "
                    "(id,fingerprint,scope_kind,owner_email,kind,title,summary,evidence,entities,"
                    "signal_count,confidence,first_observed_at,last_observed_at) "
                    "VALUES (gen_random_uuid(),%s,'company',NULL,'term_binding_proposal',%s,%s,%s::jsonb,%s::jsonb,"
                    "%s,%s,now(),now()) "
                    "ON CONFLICT (scope_kind,(coalesce(owner_email,'')),fingerprint) DO UPDATE SET "
                    "summary=EXCLUDED.summary,evidence=EXCLUDED.evidence,signal_count=EXCLUDED.signal_count,"
                    "confidence=EXCLUDED.confidence,last_observed_at=now(),updated_at=now() "
                    "RETURNING id::text AS id",
                    (
                        f"term_binding:{term}|{finding.get('referent_key')}",
                        f"\u201c{term}\u201d looks like it means {label}",
                        summary,
                        json.dumps(dict(evidence), default=str),
                        json.dumps([{"kind": referent.get("kind"), "ref": finding.get("referent_key")}]),
                        mentions,
                        round(min(0.95, dominance), 3),
                    ),
                ).fetchone()
            observation_id = str(observation["id"])
            decision = calliope_decisions.create_from_observation(
                conn_factory,
                session_id,
                observation_id,
                question,
                [
                    {"key": "approve", "label": f"Bind \u201c{term}\u201d to {label}",
                     "summary": "Becomes an affirmed binding, receipted with your name; taught meaning, not memory."},
                    {"key": "set_aside", "label": "Set aside",
                     "summary": "Not a real term here, or not this referent. The miner stops asking for 90 days."},
                ],
                summary=summary,
                decision_kind="approval",
                recommended_option="approve" if dominance >= 0.85 else None,
                urgency="normal",
                context={"finding_id": finding["id"], "term": term, "referent": dict(referent),
                         "referent_key": finding.get("referent_key"), "source": "resolution-ledger-miner"},
                created_by="calliope@meaning-ledger",
            )
            decision_id = str((decision or {}).get("id") or "")
            if not decision_id:
                raise RuntimeError("decision card returned no id")
            with conn_factory() as conn:
                conn.execute(
                    "UPDATE rvbbit.calliope_term_binding_findings "
                    "SET decision_id=%s::uuid, observation_id=%s::uuid, updated_at=now() WHERE id=%s::uuid",
                    (decision_id, observation_id, finding["id"]),
                )
            out["cards"] += 1
        except Exception as exc:  # noqa: BLE001 - one bad card must not stop the court
            out["errors"].append(f"{term}: {str(exc)[:160]}")
    # Answered cards → the ledger's two verbs.
    with conn_factory() as conn:
        answered = conn.execute(
            "SELECT f.id::text AS id, f.term_norm, d.selected_option, d.answered_by, d.response_note "
            "FROM rvbbit.calliope_term_binding_findings f "
            "JOIN rvbbit.calliope_decision_requests d ON d.id=f.decision_id "
            "WHERE f.resolved_at IS NULL AND d.status='answered'",
        ).fetchall()
        for row in answered:
            choice = str(row.get("selected_option") or "").strip().lower()
            actor = str(row.get("answered_by") or "operator")
            note = str(row.get("response_note") or "")
            try:
                if choice == "approve":
                    conn.execute("SELECT rvbbit.term_binding_affirm(%s::uuid, %s, NULL)", (row["id"], actor))
                    out["affirmed"] += 1
                else:
                    conn.execute("SELECT rvbbit.term_binding_decline(%s::uuid, %s, %s)", (row["id"], actor, note or None))
                    out["declined"] += 1
            except Exception as exc:  # noqa: BLE001
                out["errors"].append(f"{row.get('term_norm')}: {str(exc)[:160]}")
    _log(f"ledger court: {out['cards']} cards, {out['affirmed']} affirmed, {out['declined']} set aside, "
         f"{out['skipped']} skipped, {len(out['errors'])} errors")
    return out


def tick(conn_factory: Callable[[], Any], client: Any, *, budget_usd: float, max_items: int = 3) -> list[dict[str, Any]]:
    """Do up to max_items pieces of queued narrative work, budget permitting."""
    enrich_budget = float(os.environ.get("WAREHOUSE_MEANING_ENRICH_BUDGET_USD", "2") or 2)
    enrich_spent = 0.0
    with conn_factory() as conn:
        spent = float(conn.execute("SELECT rvbbit.meaning_narrative_spend() AS s").fetchone()["s"] or 0)
        if spent >= budget_usd:
            return [{"status": "budget", "spent_24h_usd": spent, "budget_usd": budget_usd}]
        try:
            enrich_spent = float(conn.execute("SELECT rvbbit.meaning_enrich_spend() AS s").fetchone()["s"] or 0)
        except Exception:  # noqa: BLE001 - 0449 not applied yet; the queue cannot emit enrich either
            enrich_spent = 0.0
        rows = conn.execute("SELECT kind, key, schema_name, members, member_fingerprint, reason FROM rvbbit.meaning_narrative_queue(%s)",
                            (max_items,)).fetchall()
    results = []
    for row in rows:
        if _stop_event.is_set():
            break
        kind = str(row["kind"])
        selected = (getattr(client, "_map_client", None) if kind == "map" else
                    getattr(client, "_enrich_client", None) if kind == "enrich" else None) or client
        accounted = AccountedClient(selected, conn_factory, budget_usd, kind, str(row["key"]), str(row["reason"]))
        try:
            if kind == "recognition":
                results.append(run_recognition(conn_factory, accounted, str(row["schema_name"])))
            elif kind == "map":
                results.append(run_map(conn_factory, accounted, str(row["member_fingerprint"] or "")))
                # Membership additions can invalidate this queue snapshot.
                break
            elif kind == "dossier":
                outcome = run_dossier(conn_factory, accounted, str(row["key"]))
                if outcome is not None:
                    results.append(outcome)
            elif kind == "enrich":
                if not enrich_enabled():
                    continue
                with conn_factory() as conn:
                    enrich_spent = float(conn.execute("SELECT rvbbit.meaning_enrich_spend() AS s").fetchone()["s"] or 0)
                if enrich_spent >= enrich_budget:
                    _log(f"enrich budget reached (${enrich_spent:.2f} of ${enrich_budget:.2f}); skipping enrich work")
                    continue
                outcome = run_enrich(conn_factory, accounted, str(row["key"]))
                if outcome is not None:
                    results.append(outcome)
            elif kind == "ledger":
                # Resolution Ledger court: turn miner proposals into Awaiting You
                # cards and apply the answers. No model, no budget — SQL and the
                # decisions module only.
                results.append(run_ledger_court(conn_factory))
            elif kind == "domain":
                results.append(run_domain(conn_factory, accounted, str(row["key"]), str(row["schema_name"]),
                                          list(row["members"] or []), str(row["member_fingerprint"] or "")))
            else:
                # A queue-shape migration deployed ahead of this worker once turned
                # unknown kinds into cluster domains ("Unknown - Empty Cluster"
                # rows, 2026-09-19). An unknown kind is now skipped out loud.
                _log(f"queue kind {kind!r} is unknown to this worker; skipped")
        except BudgetExceeded:
            with conn_factory() as conn:
                spent = float(conn.execute("SELECT rvbbit.meaning_narrative_spend() AS s").fetchone()["s"] or 0)
            results.append({"status": "budget", "spent_24h_usd": spent, "budget_usd": budget_usd})
            break
    # Place the remaining tables only after their source has entered the map.
    try:
        with conn_factory() as conn:
            pending = conn.execute(
                "SELECT count(*) AS n FROM rvbbit.meaning_relations m WHERE m.status = 'judged' AND m.domain_key IS NULL "
                "AND m.placed_by IS DISTINCT FROM 'override' AND coalesce(m.plumbing, 0) < 0.7 AND coalesce(m.kind, '') <> 'system_plumbing' "
                "AND NOT (coalesce(m.placed_by, '') = 'jev:unclear' AND m.updated_at > now() - interval '24 hours') "
                "AND EXISTS (SELECT 1 FROM rvbbit.meaning_company_map WHERE status = 'done')").fetchone()["n"]
            if int(pending or 0) > 0:
                assigned = assign_mapped_sources(conn)
                _log(f"assignment: {assigned}")
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        _log(f"assignment skipped: {type(exc).__name__}: {exc}")
    # Compatibility hook: 0458 requires explicit merges and never auto-folds identities.
    try:
        with conn_factory() as conn:
            left = conn.execute("SELECT count(*) AS n FROM rvbbit.meaning_narrative_queue(50) WHERE kind = 'dossier'").fetchone()["n"]
            if rows and int(left or 0) == 0 and any(r.get("domain_key") for r in results):
                folded = conn.execute("SELECT rvbbit.meaning_reconcile_subjects() AS r").fetchone()["r"]
                if folded and int((folded or {}).get("folded") or 0):
                    _log(f"reconcile: {folded}")
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001
        _log(f"reconcile skipped: {type(exc).__name__}: {exc}")
    return results



FALLBACK_BACKEND = "clover_llm"
# The strong Clover route (Luna Pro, thinking on). The backend's registered
# model ("clover", the private Gemma) and "calliope" (Luna without thinking)
# are too weak to recognize a source or lay out a company map well; they are
# only the last resort when the strong route is not allowed on this backend.
FALLBACK_MODEL = "calliope-luna"
_ANNOUNCED_BACKENDS: set[str] = set()


def _registered_model(dsn: str, backend: str) -> str:
    """The model a backend is registered to serve (transport_opts.model), or ''."""
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT transport_opts->>'model' FROM rvbbit.backends WHERE name=%s", (backend,)
            ).fetchone()
        return str((row or [""])[0] or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _connect_client(dsn: str, backend: str, model: str, request_user: str, timeout_seconds: float) -> Any:
    from business_topology.worker import HutchChatClient  # shipped in the image beside business_topology_jobs

    client = HutchChatClient.from_postgres(
        dsn, backend_name=backend, model=model, request_user=request_user,
        timeout_seconds=timeout_seconds, transport_attempts=1,
    )
    # a pass-through openai_chat backend has no x-hutch-model-version header
    client.strict_model_version = False
    return client


def _backend_unusable(exc: BaseException) -> bool:
    text = str(exc)
    return "no usable token" in text or "was not found" in text or "is not registered for backend" in text


def build_client(dsn: str, request_user: str = "meaning-narrative-worker", model_env: str = "WAREHOUSE_MEANING_NARRATIVE_MODEL",
                 timeout_seconds: float | None = None, default_model: str | None = None,
                 fallback_model: str | None = None, backend_env: str | None = None,
                 default_backend: str | None = None) -> Any:
    """Direct, tool-less chat completion.

    The configured backend (WAREHOUSE_MEANING_NARRATIVE_BACKEND, default
    OpenRouter) is tried first. An appliance that keeps its model keys in
    Hutch has no OpenRouter token on the box at all (a hosted install,
    2026-09-21: every narrative tick failed for a day, nothing narrated), so
    when that backend has no usable token, is not registered, or does not
    serve the model, the worker falls back to the appliance's own Clover
    backend and the model it is registered to serve. Meaning then just works
    wherever Clover does; a box that does hold an OpenRouter key keeps it.
    """
    model = os.environ.get(model_env, "").strip() or default_model or DEFAULT_MODEL
    backend = ((os.environ.get(backend_env, "").strip() if backend_env else "")
               or os.environ.get("WAREHOUSE_MEANING_NARRATIVE_BACKEND", "").strip()
               or default_backend or DEFAULT_BACKEND)
    timeout = float(timeout_seconds if timeout_seconds is not None
                    else (os.environ.get("WAREHOUSE_MEANING_NARRATIVE_TIMEOUT_SECONDS", "300") or 300))
    try:
        return _connect_client(dsn, backend, model, request_user, timeout)
    except RuntimeError as exc:
        if backend == FALLBACK_BACKEND or not _backend_unusable(exc):
            raise
        preferred = (fallback_model or os.environ.get("WAREHOUSE_MEANING_NARRATIVE_FALLBACK_MODEL", "").strip()
                     or FALLBACK_MODEL)
        try:
            client = _connect_client(dsn, FALLBACK_BACKEND, preferred, request_user, timeout)
            fallback_model = preferred
        except RuntimeError as inner:
            if "is not registered for backend" not in str(inner):
                raise
            fallback_model = _registered_model(dsn, FALLBACK_BACKEND) or "clover"
            client = _connect_client(dsn, FALLBACK_BACKEND, fallback_model, request_user, timeout)
        key = f"{backend}->{FALLBACK_BACKEND}:{fallback_model}"
        if key not in _ANNOUNCED_BACKENDS:
            _ANNOUNCED_BACKENDS.add(key)
            _log(f"backend {backend!r} unusable here ({exc}); narrating through {FALLBACK_BACKEND!r} as {fallback_model!r}")
        return client


_MAP_CLIENT: Any = None
_ENRICH_CLIENT: Any = None


def enrich_enabled() -> bool:
    return _env_bool("WAREHOUSE_MEANING_ENRICH", True)


def enrich_client(dsn: str, default: Any) -> Any:
    """The high-judgement route for proposals.

    Proposing a classification is the one place in Meaning where a weak model
    shows: it invents categories the values do not support. Jev still answers
    every row, Luna still writes the bulk narrative; this asks for Sol both as
    the configured model and as the Clover fallback.
    """
    global _ENRICH_CLIENT
    model = os.environ.get("WAREHOUSE_MEANING_ENRICH_MODEL", "").strip() or ENRICH_MODEL
    if _ENRICH_CLIENT is None or getattr(_ENRICH_CLIENT, "model", None) != model:
        try:
            # Sol is a Clover route, so ask Clover for it by default rather than
            # whatever serves the bulk narrative. An appliance with an OpenRouter
            # key and no model restriction on that backend would otherwise CONNECT
            # happily and fail on every request, because the fallback ladder only
            # catches refusals at connect time (the AWS pilot, 2026-09-21:
            # backends.openrouter.transport_opts is {}).
            _ENRICH_CLIENT = build_client(
                dsn, request_user="meaning-enrich", model_env="WAREHOUSE_MEANING_ENRICH_MODEL",
                timeout_seconds=300.0, default_model=model, fallback_model=model,
                backend_env="WAREHOUSE_MEANING_ENRICH_BACKEND", default_backend=FALLBACK_BACKEND)
        except RuntimeError as exc:
            _log(f"enrich route {model!r} unavailable ({exc}); proposing with {getattr(default, 'model', '?')!r}")
            return default
    return _ENRICH_CLIENT


def map_client(dsn: str, default: Any) -> Any:
    global _MAP_CLIENT
    model = os.environ.get("WAREHOUSE_MEANING_MAP_MODEL", "").strip()
    if not model or model == default.model:
        return default
    if _MAP_CLIENT is None or _MAP_CLIENT.model != model:
        _MAP_CLIENT = build_client(dsn, request_user="meaning-map", model_env="WAREHOUSE_MEANING_MAP_MODEL", timeout_seconds=600.0)
    return _MAP_CLIENT


def _worker_loop(dsn: str) -> None:
    import psycopg
    from psycopg.rows import dict_row

    def conn_factory():
        return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)

    budget = float(os.environ.get("WAREHOUSE_MEANING_NARRATIVE_BUDGET_USD", "5") or 5)
    client = None
    idle_backoff = TICK_SECONDS
    while not _stop_event.is_set():
        try:
            with conn_factory() as conn:
                ready = conn.execute("SELECT to_regprocedure('rvbbit.meaning_narrative_queue(integer)') IS NOT NULL AS ok").fetchone()["ok"]
            if not ready:
                _stop_event.wait(300)
                continue
            if client is None:
                client = build_client(dsn)
                client._map_client = map_client(dsn, client)
                client._enrich_client = enrich_client(dsn, client) if enrich_enabled() else client
                _log(f"direct route ready: backend={client.backend_name} model={client.model} "
                     f"map_model={client._map_client.model} "
                     f"enrich_model={getattr(client._enrich_client, 'model', 'off')} budget=${budget:.2f}/day")
            with conn_factory() as lease:
                if not lease.execute("SELECT pg_try_advisory_lock(hashtext('rvbbit.meaning.worker')) AS ok").fetchone()["ok"]:
                    _stop_event.wait(TICK_SECONDS)
                    continue
                results = tick(conn_factory, client, budget_usd=budget)
            if results and results[-1].get("status") == "budget":
                _log(f"daily budget reached (${results[-1]['spent_24h_usd']:.2f} of ${budget:.2f}); sleeping an hour")
                _stop_event.wait(3600)
                continue
            idle_backoff = TICK_SECONDS if results else min(idle_backoff * 2, 900)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            _log(f"tick failed: {type(exc).__name__}: {exc}")
            client = None
            idle_backoff = min(max(idle_backoff, TICK_SECONDS) * 2, 900)
        _stop_event.wait(idle_backoff)


def start_worker(dsn: str) -> bool:
    global _worker_thread
    if not _env_bool("WAREHOUSE_MEANING_NARRATIVE_WORKER", True):
        return False
    if _worker_thread and _worker_thread.is_alive():
        return True
    _stop_event.clear()
    _worker_thread = threading.Thread(target=_worker_loop, args=(dsn,), name="meaning-narrative", daemon=True)
    _worker_thread.start()
    return True


def stop_worker() -> None:
    _stop_event.set()
