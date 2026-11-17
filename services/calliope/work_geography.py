"""Work geography: which governed domains a session's SQL actually touched.

The bridge between the explicit (pieces of work on the Floor) and the
abstract (the Briarpatch's territory). A deterministic worker walks the
mirrored Hermes tool calls incrementally, pulls every SQL string out of
run_sql-family invocations, extracts the relations it references, and
intersects them with the knowledge loop's governed-domain catalog
(business_value_graph_summary.source_relations). Matches are upserted into
rvbbit.work_session_domains keyed by Calliope session, which work_graph
items already carry — so any piece joins to territory in one hop, and
rvbbit.work_graph_geography (0392) serves the whole chain with coordinates
from briarpatch_anchors.

Parsing doctrine: the catalog is the validator, not the parser. sqlglot
runs tolerant (custom rvbbit functions parse as anonymous nodes; a bare
custom infix operator loses its predicate, never its FROM clause), a regex
sweep backstops total parse failure, and everything extracted is filtered
through the source_relations whitelist — so junk tokens cannot map to a
domain that does not own them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from typing import Any, Callable

TICK_SECONDS = int(os.environ.get("WORK_GEOGRAPHY_TICK_SECONDS", "300"))
WATERMARK_KEY = "work_geography_watermark"
BATCH_LIMIT = 4_000
SQL_ARG_KEYS = {"sql", "query", "statement", "statements"}
SQL_TOOL_RE = re.compile(r"run_sql|sql_multi|execute_sql", re.I)
RELATION_RE = re.compile(r"\b([a-z_][a-z0-9_$]*)\.([a-z_][a-z0-9_$]*)\b", re.I)


def _iter_sql_strings(value: Any, depth: int = 0) -> list[str]:
    """Collect SQL-looking strings from a tool-call arguments payload.
    Arguments arrive as JSON text, often with nested JSON-encoded strings
    (the dynamic `tool_call` dispatcher wraps the real tool's arguments),
    so string values that look like JSON are re-parsed and walked too."""
    if depth > 6:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in SQL_ARG_KEYS:
                if isinstance(item, str) and item.strip():
                    found.append(item)
                elif isinstance(item, list):
                    found.extend(s for s in item if isinstance(s, str) and s.strip())
            else:
                found.extend(_iter_sql_strings(item, depth + 1))
    elif isinstance(value, list):
        for item in value:
            found.extend(_iter_sql_strings(item, depth + 1))
    elif isinstance(value, str):
        stripped = value.strip()
        if stripped[:1] in "{[":
            try:
                found.extend(_iter_sql_strings(json.loads(stripped), depth + 1))
            except Exception:  # noqa: BLE001
                pass
    return found


def extract_relations(sql: str) -> set[str]:
    """schema.table names referenced by one SQL string, lowercased.
    Tolerant parse first; regex sweep when even that fails. CTE aliases and
    other junk survive here — the catalog intersect downstream removes them."""
    relations: set[str] = set()
    try:
        import sqlglot
        from sqlglot import exp
        from sqlglot.errors import ErrorLevel

        trees = sqlglot.parse(sql, read="postgres", error_level=ErrorLevel.IGNORE)
        for tree in trees:
            if tree is None:
                continue
            for table in tree.find_all(exp.Table):
                name = ".".join(part for part in (table.db, table.name) if part)
                if name:
                    relations.add(name.lower())
    except Exception:  # noqa: BLE001
        pass
    if not relations:
        relations.update(
            f"{match.group(1)}.{match.group(2)}".lower()
            for match in RELATION_RE.finditer(sql)
        )
    return relations


def _domain_catalog(conn: Any) -> dict[str, set[str]]:
    """relation ('schema.table') -> domain_keys that claim it. All statuses:
    territory mapping is about where work happened, not adoption standing."""
    catalog: dict[str, set[str]] = {}
    for row in conn.execute(
        "SELECT domain_key, source_relations FROM rvbbit.business_value_graph_summary "
        "WHERE source_relations IS NOT NULL"
    ).fetchall():
        for relation in row["source_relations"] or []:
            catalog.setdefault(str(relation).lower(), set()).add(str(row["domain_key"]))
    return catalog


def _bare_name_index(catalog: dict[str, set[str]]) -> dict[str, str]:
    """table -> full relation, only where the bare name is unambiguous —
    unqualified references in evidence SQL resolve through this."""
    seen: dict[str, str | None] = {}
    for relation in catalog:
        bare = relation.split(".", 1)[-1]
        seen[bare] = None if bare in seen else relation
    return {bare: full for bare, full in seen.items() if full}


def tick(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    with conn_factory() as conn:
        ready = conn.execute(
            "SELECT to_regclass('rvbbit.work_session_domains') IS NOT NULL "
            "AND to_regclass('rvbbit.hermes_messages') IS NOT NULL "
            "AND to_regclass('rvbbit.business_value_graph_summary') IS NOT NULL AS r"
        ).fetchone()["r"]
        if not ready:
            return {"status": "not_ready"}
        catalog = _domain_catalog(conn)
        if not catalog:
            return {"status": "no_catalog"}
        bare_index = _bare_name_index(catalog)
        watermark_row = conn.execute(
            "SELECT value->>'ts' AS ts FROM rvbbit.settings WHERE key=%s",
            (WATERMARK_KEY,),
        ).fetchone()
        watermark = (watermark_row or {}).get("ts") or "1970-01-01T00:00:00Z"

        rows = conn.execute(
            """
            SELECT m.session_id AS hermes_session_id, m.ts,
                   tc->'function'->>'name' AS tool_name,
                   tc->'function'->>'arguments' AS arguments
              FROM rvbbit.hermes_messages m
             CROSS JOIN LATERAL jsonb_array_elements(m.tool_calls) tc
             WHERE m.ts > %s::timestamptz AND m.tool_call_count > 0
             ORDER BY m.ts
             LIMIT %s
            """,
            (watermark, BATCH_LIMIT),
        ).fetchall()
        if not rows:
            return {"status": "idle", "watermark": watermark}

        # hermes session -> {domain_key: {relations}}
        evidence: dict[str, dict[str, set[str]]] = {}
        max_ts = watermark
        scanned = 0
        for row in rows:
            max_ts = max(max_ts, str(row["ts"]))
            name = str(row["tool_name"] or "")
            arguments = str(row["arguments"] or "")
            # Cheap gate only — nested dispatcher payloads escape their
            # quotes (\"sql\"), so match loosely and let the key-scoped
            # walker decide what is actually a SQL argument.
            if not SQL_TOOL_RE.search(name) and "sql" not in arguments.lower():
                continue
            try:
                payload = json.loads(arguments)
            except Exception:  # noqa: BLE001
                continue
            for sql in _iter_sql_strings(payload):
                scanned += 1
                for relation in extract_relations(sql):
                    full = relation if relation in catalog else None
                    if full is None and "." not in relation:
                        full = bare_index.get(relation)
                    if full is None:
                        continue
                    per_session = evidence.setdefault(
                        str(row["hermes_session_id"]), {}
                    )
                    for domain_key in catalog[full]:
                        per_session.setdefault(domain_key, set()).add(full)

        upserts = 0
        if evidence:
            mapping = {
                str(row["hermes_session_id"]): str(row["id"])
                for row in conn.execute(
                    "SELECT id, hermes_session_id FROM rvbbit.calliope_sessions "
                    "WHERE hermes_session_id = ANY(%s)",
                    (list(evidence),),
                ).fetchall()
            }
            for hermes_id, domains in evidence.items():
                session_id = mapping.get(hermes_id)
                if not session_id:
                    continue
                for domain_key, relations in domains.items():
                    conn.execute(
                        """
                        INSERT INTO rvbbit.work_session_domains
                            (session_id, domain_key, evidence_count, relations,
                             first_seen_at, last_seen_at)
                        VALUES (%s::uuid, %s, %s, %s, now(), now())
                        ON CONFLICT (session_id, domain_key) DO UPDATE SET
                            evidence_count = rvbbit.work_session_domains.evidence_count
                                             + EXCLUDED.evidence_count,
                            relations = ARRAY(
                                SELECT DISTINCT unnest(
                                    rvbbit.work_session_domains.relations
                                    || EXCLUDED.relations)
                                ORDER BY 1),
                            last_seen_at = now()
                        """,
                        (session_id, domain_key, len(relations), sorted(relations)),
                    )
                    upserts += 1
        conn.execute(
            "INSERT INTO rvbbit.settings (key, value, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=now()",
            (WATERMARK_KEY, json.dumps({"ts": max_ts})),
        )
    return {
        "status": "mapped" if upserts else "scanned",
        "messages": len(rows),
        "sql_strings": scanned,
        "upserts": upserts,
        "watermark": max_ts,
    }


_WORKER_STARTED = False


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return False

    def _loop() -> None:
        time.sleep(120)
        while True:
            try:
                result = tick(conn_factory)
                if result.get("upserts"):
                    print(f"work_geography: {json.dumps(result)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"work_geography tick failed: {exc}", file=sys.stderr)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=_loop, name="work-geography", daemon=True).start()
    _WORKER_STARTED = True
    return True
