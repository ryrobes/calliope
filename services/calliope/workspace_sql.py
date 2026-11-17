"""Ordinary derived-data work, executed atomically as a dedicated database login.

The PostgreSQL parser selects supported operations; database privileges enforce
ownership. This is a trusted appliance workspace, not an untrusted SQL sandbox.
In particular, source views, RLS policies and extension code are administrator
installed code. Never grant this login membership in an administrator role.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from pglast.parser import parse_sql_json, split
from pglast import ast, parse_sql
from pglast.stream import RawStream
from pglast.visitors import Visitor

SCHEMA = "calliope"  # Legacy default for data work.
SCHEMAS = (SCHEMA, "calliope_apps")
MAX_STATEMENTS = 50
MAX_CHARS = 100_000
ROW_CAP = 200
RUNTIME_SCHEMA = "calliope_runtime"

# The decision family: bounded local helpers workspace SQL may call without
# being registered semantic operators. Two kinds, and neither belongs in
# rvbbit.operators, which exists to carry cache policy and cost.
#
#   Constructors build JSON and call nothing at all: leaf, branch,
#   choice_branch, truth_question, choice_question.
#
#   Callers meter entirely through the already-registered rvbbit.clover_decide
#   operator: truth, truth_score and choose ask it one question, decision_tree
#   and decision_tree_batch walk a bounded path of them under limits the
#   functions enforce themselves (256 states and 8 MiB per batch, 32 KiB per
#   state and history, 16 steps).
#
# Note that the scalar callers are per-row and do not batch: one round trip per
# row, so a large table costs wall clock rather than safety. decision_tree_batch
# is the shape that scales.
#
# This is a named family, never a wildcard over rvbbit.*, and every other
# guarantee is untouched — the call still runs through a SECURITY DEFINER
# adapter, writes stay inside the workspace schemas, receipts and memoization
# are unchanged, and a failed batch still rolls back.
TRUSTED_LOCAL_FUNCTIONS = frozenset({
    ("rvbbit", "leaf"),
    ("rvbbit", "branch"),
    ("rvbbit", "choice_branch"),
    ("rvbbit", "truth_question"),
    ("rvbbit", "choice_question"),
    ("rvbbit", "truth"),
    ("rvbbit", "truth_score"),
    ("rvbbit", "choose"),
    ("rvbbit", "decision_tree"),
    ("rvbbit", "decision_tree_batch"),
})
def _runtime_name(schema, name):
    if schema == "rvbbit":
        return name
    return "mcp_" + hashlib.sha256(schema.encode()).hexdigest()[:12] + "_" + name[:46]


class _RuntimeOperators(Visitor):
    def __init__(self, operators, adapters=None):
        super().__init__()
        self.adapters = adapters or {}
        self.operators = {tuple(item) if not isinstance(item, str) else ("rvbbit", item) for item in operators}

    def visit_FuncCall(self, ancestors, node):
        names = tuple(part.sval for part in node.funcname)
        if names in self.operators:
            node.funcname = (ast.String(sval=RUNTIME_SCHEMA), ast.String(sval=self.adapters.get(names) or _runtime_name(*names)))


def _execution_sql(statement, operators, adapters=None):
    if not operators:
        return statement
    tree = parse_sql(statement)
    _RuntimeOperators(operators, adapters)(tree)
    return RawStream()(tree)


def _housekeeping_sql():
    name = "0431_workspace_ddl_housekeeping.sql"
    path = Path(__file__).with_name("sql") / name
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "crates/pg_rvbbit/sql/migrations" / name
    return path.read_text()


def _walk(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _names(value):
    return [item["String"]["sval"] for item in value]


def _targets(kind, node):
    if kind in {"CreateStmt", "ViewStmt", "IndexStmt", "InsertStmt", "UpdateStmt",
                "DeleteStmt", "MergeStmt", "AlterTableStmt", "RefreshMatViewStmt",
                "RenameStmt", "CreateSeqStmt", "AlterSeqStmt"}:
        return [node.get("relation") or node.get("view") or node.get("sequence") or {}]
    if kind == "CreateTableAsStmt":
        return [node["into"]["rel"]]
    if kind == "SelectStmt":
        return [node["intoClause"]["rel"]] if node.get("intoClause") else []
    if kind == "TruncateStmt":
        return [item["RangeVar"] for item in node.get("relations", [])]
    if kind == "VacuumStmt" and not node.get("is_vacuumcmd"):
        if not node.get("rels"):
            raise ValueError("ANALYZE must name specific workspace tables")
        return [item["VacuumRelation"]["relation"] for item in node.get("rels", [])]
    if kind in {"DropStmt", "CommentStmt"}:
        object_type = node.get("removeType") or node.get("objtype")
        if object_type not in {"OBJECT_TABLE", "OBJECT_VIEW", "OBJECT_MATVIEW",
                               "OBJECT_INDEX", "OBJECT_SEQUENCE", "OBJECT_COLUMN"}:
            raise ValueError("Only workspace tables, views, indexes, sequences and columns are supported")
        objects = node.get("objects") or [node.get("object", {})]
        targets = []
        for obj in objects:
            names = _names(obj.get("List", {}).get("items", []))
            if len(names) < 2:
                raise ValueError("Qualify workspace objects as calliope.object_name or calliope_apps.object_name")
            targets.append({"schemaname": names[0], "relname": names[1]})
        return targets
    raise ValueError(f"{kind.removesuffix('Stmt')} is outside workspace SQL; use a typed administration tool if needed")


def plan(raw_sql: Any) -> dict:
    statement = str(raw_sql or "").strip()
    if not statement or len(statement) > MAX_CHARS or "\x00" in statement:
        raise ValueError(f"Provide SQL between 1 and {MAX_CHARS} characters")
    try:
        parsed = json.loads(parse_sql_json(statement))["stmts"]
        statements = list(split(statement))
    except Exception as exc:
        raise ValueError(f"Invalid PostgreSQL SQL: {exc}") from None
    if not parsed or len(parsed) > MAX_STATEMENTS:
        raise ValueError(f"Use between 1 and {MAX_STATEMENTS} statements per atomic batch")
    writes, reads, functions, operators = set(), set(), set(), set()
    for raw in parsed:
        kind, node = next(iter(raw["stmt"].items()))
        _targets(kind, node)  # reject unsupported top-level commands, including transaction control
        for key, value in _walk(raw["stmt"]):
            if key[:1].isupper() and key.endswith("Stmt") and isinstance(value, dict):
                for target in _targets(key, value):
                    if target.get("schemaname") not in SCHEMAS or target.get("catalogname"):
                        raise ValueError("Workspace writes must target explicitly qualified calliope or calliope_apps objects")
                    if target.get("relpersistence") == "t":
                        raise ValueError("Use durable workspace tables; temporary tables disappear after the batch")
                    writes.add((target["schemaname"], target["relname"]))
            if key == "RangeVar" and value.get("schemaname"):
                if value.get("catalogname"):
                    raise ValueError("Cross-database references are not supported")
                reads.add((value["schemaname"], value["relname"]))
            if key == "behavior" and value == "DROP_CASCADE":
                raise ValueError("Use RESTRICT; CASCADE can remove dependents outside the workspace")
            if key in {"tablespacename", "tableSpaceName"} and value:
                raise ValueError("Workspace objects use the database's default tablespace")
            if key == "subtype" and value in {"AT_ChangeOwner", "AT_SetTableSpace", "AT_AttachPartition", "AT_DetachPartition", "AT_AddInherit", "AT_DropInherit"}:
                raise ValueError("Changing ownership, tablespace or external inheritance is administration work")
            if key == "FuncCall":
                names = _names(value["funcname"])
                if len(names) == 2 and names[0] not in {"pg_catalog", RUNTIME_SCHEMA}:
                    # Resolve against installed catalogs at execution time. MCP
                    # registrations and generated signatures change independently
                    # of Calliope releases; names are not an application allowlist.
                    operators.add(tuple(names))
                    continue
                if len(names) > 2 or (len(names) == 2 and names[0] == RUNTIME_SCHEMA):
                    raise ValueError("Use the registered function's original schema-qualified name")
                functions.add(names[-1])
            if key in {"TypeName", "typeName"}:
                names = _names(value.get("names", []))
                if len(names) > 1 and names[0] != "pg_catalog":
                    raise ValueError("Use PostgreSQL built-in column types in workspace SQL")
            if key == "A_Expr":
                names = _names(value.get("name", []))
                if len(names) > 1 and names[0] != "pg_catalog":
                    raise ValueError("Use PostgreSQL built-in operators in workspace SQL")
    return {"sql": statement, "sql_sha256": hashlib.sha256(statement.encode()).hexdigest(),
            "statements": statements, "writes": sorted(writes), "reads": sorted(reads - writes),
            "functions": sorted(functions), "operators": sorted(operators)}


def is_workspace_write(raw_sql: Any) -> bool:
    """Route existing administration calls without reclassifying ordinary SELECTs.

    Mixed workspace/administration batches deliberately enter the workspace
    validator and fail, rather than creating an approval for a half-valid batch.
    """
    try:
        parsed = json.loads(parse_sql_json(str(raw_sql or "")))
        for kind, node in _walk(parsed):
            if kind[:1].isupper() and kind.endswith("Stmt") and isinstance(node, dict):
                try:
                    if any(t.get("schemaname") in SCHEMAS for t in _targets(kind, node)):
                        return True
                except ValueError:
                    pass
    except Exception:
        pass
    return False


def _prepare_operators(conn, role, operators):
    if not operators:
        return {}
    adapters = {}
    owner = conn.execute("SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname=%s", (RUNTIME_SCHEMA,)).fetchone()
    if owner and owner["owner"] != conn.info.user:
        raise RuntimeError("Workspace runtime schema has an unexpected owner")
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(sql.Identifier(RUNTIME_SCHEMA), sql.Identifier(conn.info.user)))
    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(RUNTIME_SCHEMA)))
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(RUNTIME_SCHEMA), sql.Identifier(role)))
    conn.execute("SET LOCAL search_path = pg_catalog")
    for reference in operators:
        schema, name = ("rvbbit", reference) if isinstance(reference, str) else reference
        registered = (schema, name) in TRUSTED_LOCAL_FUNCTIONS
        if not registered and schema == "rvbbit" and conn.execute("SELECT to_regclass('rvbbit.operators') AS tbl").fetchone()["tbl"]:
            registered = bool(conn.execute("SELECT 1 FROM rvbbit.operators WHERE name=%s", (name,)).fetchone())
        if not registered and conn.execute("SELECT to_regclass('rvbbit.mcp_tools') AS tbl").fetchone()["tbl"]:
            registered = bool(conn.execute("SELECT 1 FROM rvbbit.mcp_tools WHERE server=%s AND name=%s", (schema, name)).fetchone())
        if not registered:
            raise ValueError(f"Function {schema}.{name} is not a registered RVBBIT operator or MCP tool; refresh its registration if needed")
        signatures = conn.execute("""
            SELECT p.oid, pg_get_function_arguments(p.oid) AS arguments,
                   pg_get_function_result(p.oid) AS result,
                   pg_get_function_identity_arguments(p.oid) AS identity,
                   p.pronargs, p.proretset, p.provariadic, p.proisstrict,
                   p.proargmodes, p.prokind
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname=%s AND p.proname=%s ORDER BY p.oid
        """, (schema, name)).fetchall()
        if not signatures:
            raise ValueError(f"Registered function {schema}.{name} has no installed SQL signature; regenerate its SQL wrappers")
        # Version the adapter family when signatures/defaults change. Existing
        # views keep their adapters; stale overloads cannot hijack a new call.
        fingerprint = hashlib.sha256(json.dumps([schema, name, signatures], sort_keys=True, default=str).encode()).hexdigest()[:16]
        adapter_name = _runtime_name(schema, name)[:46] + "_" + fingerprint
        adapters[(schema, name)] = adapter_name
        for fn in signatures:
            if fn["prokind"] != "f" or (fn["proargmodes"] and any(m not in {"i", "v"} for m in fn["proargmodes"])):
                raise ValueError(f"Registered function {schema}.{name} has unsupported output arguments")
            # Delegate to the installed function, preserving overloads, named
            # arguments, defaults, NULL behavior and scalar/set-returning shape.
            # No source credentials or runtime table grants reach the login.
            args = [sql.SQL(("VARIADIC " if fn["provariadic"] and i == fn["pronargs"] else "") + f"${i}")
                    for i in range(1, fn["pronargs"] + 1)]
            body = sql.SQL("SELECT {}{}.{}({})").format(
                sql.SQL("* FROM " if fn["proretset"] else ""), sql.Identifier(schema),
                sql.Identifier(name), sql.SQL(", ").join(args))
            adapter = sql.SQL("{}.{}").format(sql.Identifier(RUNTIME_SCHEMA), sql.Identifier(adapter_name))
            conn.execute(sql.SQL("CREATE OR REPLACE FUNCTION {}({}) RETURNS {} LANGUAGE sql {} SECURITY DEFINER "
                                 "SET search_path = pg_catalog AS {} ").format(
                adapter, sql.SQL(fn["arguments"]), sql.SQL(fn["result"]),
                sql.SQL("STRICT" if fn["proisstrict"] else "CALLED ON NULL INPUT"), sql.Literal(body.as_string(conn))))
            signature = sql.SQL("{}({})").format(adapter, sql.SQL(fn["identity"]))
            conn.execute(sql.SQL("REVOKE ALL ON FUNCTION {} FROM PUBLIC").format(signature))
            conn.execute(sql.SQL("GRANT EXECUTE ON FUNCTION {} TO {}").format(signature, sql.Identifier(role)))
    return adapters


def _prepare(conn_factory, batch, seed, schema_allowed):
    if not seed:
        raise RuntimeError("Workspace SQL requires the server's configured signing secret")
    with conn_factory() as conn:
        with conn.transaction():
            database = conn.info.dbname
            role = "calliope_work_" + hashlib.sha256(database.encode()).hexdigest()[:16]
            password = hmac.new(seed.encode(), f"calliope-workspace-v1:{database}".encode(), hashlib.sha256).hexdigest()
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (role,))
            # Apply the same idempotent compatibility fix as the extension's
            # migration chain, so a server-only rollout needs no PG restart.
            conn.execute(_housekeeping_sql())
            current = conn.execute("SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=%s", (role,)).fetchone()
            if current and (any(current.values()) or conn.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s", (role,)
            ).fetchone()):
                raise RuntimeError("Workspace role has unexpected elevated privileges or memberships")
            if not current:
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            else:
                conn.execute(sql.SQL("ALTER ROLE {} LOGIN NOINHERIT PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            for workspace_schema in SCHEMAS:
                owner = conn.execute("SELECT pg_get_userbyid(nspowner) AS owner FROM pg_namespace WHERE nspname=%s", (workspace_schema,)).fetchone()
                if owner and owner["owner"] != role:
                    raise RuntimeError(f"Schema {workspace_schema} already exists under another owner; inspect it before adopting it as the workspace")
                if not owner:
                    conn.execute(sql.SQL("CREATE SCHEMA {} AUTHORIZATION {}").format(sql.Identifier(workspace_schema), sql.Identifier(role)))
                    conn.execute(sql.SQL("REVOKE ALL ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(workspace_schema)))
                    conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC").format(sql.Identifier(role), sql.Identifier(workspace_schema)))
            conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), sql.Identifier(role)))
            batch["operator_adapters"] = _prepare_operators(conn, role, batch.get("operators", []))
            if conn.execute("SELECT to_regclass('rvbbit.operators') AS registry").fetchone()["registry"]:
                # Older pg_rvbbit INSERT prewarm hooks resolve function names
                # before honoring force_heap_scan/implicit_prewarm. This occurs
                # before SECURITY DEFINER adapters run. Only names are needed
                # for that lookup; operator bodies/settings remain private.
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA rvbbit TO {}").format(sql.Identifier(role)))
                conn.execute(sql.SQL("GRANT SELECT (name) ON rvbbit.operators TO {}").format(sql.Identifier(role)))
            if conn.execute("SELECT to_regclass('rvbbit.tables') AS registry").fetchone()["registry"]:
                # Index-build hooks consult this small registry even with native
                # planning selected. No grants on data, secrets or write catalogs.
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA rvbbit TO {}").format(sql.Identifier(role)))
                conn.execute(sql.SQL("GRANT SELECT ON rvbbit.tables TO {}").format(sql.Identifier(role)))
            # The existing server identity must still be able to render/inspect
            # workspace outputs when it is an ordinary non-superuser service role.
            reader = conn.info.user
            for workspace_schema in SCHEMAS:
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(workspace_schema), sql.Identifier(reader)))
                conn.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA {} GRANT SELECT ON TABLES TO {}").format(sql.Identifier(role), sql.Identifier(workspace_schema), sql.Identifier(reader)))
            for schema, relation in batch["reads"]:
                if schema in SCHEMAS:
                    continue
                if not schema_allowed(schema):
                    raise ValueError(f"Source schema {schema} is outside the exposed data schemas")
                source = conn.execute("SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relname=%s", (schema, relation)).fetchone()
                if not source or source["relkind"] not in {"r", "p", "v", "m"}:
                    raise ValueError(f"Source {schema}.{relation} must be an existing local table or view")
                conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
                conn.execute(sql.SQL("GRANT SELECT ON TABLE {}.{} TO {}").format(sql.Identifier(schema), sql.Identifier(relation), sql.Identifier(role)))
            for name in batch["functions"]:
                functions = conn.execute("SELECT provolatile,prosecdef FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='pg_catalog' AND p.proname=%s", (name,)).fetchall()
                # No dynamic SQL/configuration entry points. Built-in sequence
                # operations still require PostgreSQL privileges on the sequence.
                forbidden = name in {"set_config", "query_to_xml", "query_to_xmlschema", "query_to_xml_and_xmlschema"}
                if not functions or forbidden or any(f["prosecdef"] or (f["provolatile"] == "v" and name not in {"random", "clock_timestamp", "gen_random_uuid", "uuidv4", "uuidv7", "nextval", "currval", "lastval", "setval"}) for f in functions):
                    raise ValueError(f"Function {name} is not a supported data transformation function")
            dsn = make_conninfo(conn.info.dsn, user=role, password=password,
                                # Bypass RVBBIT's optimizer metadata hooks from
                                # connection startup, before even set_config is
                                # planned. Ordinary workspace SQL uses native
                                # PostgreSQL and needs no internal catalog grants.
                                options="-c rvbbit.force_heap_scan=on",
                                application_name="calliope-workspace", connect_timeout="10")
    return dsn, role


def execute(conn_factory, batch, *, seed, schema_allowed, timeout_ms=120_000):
    dsn, role = _prepare(conn_factory, batch, seed, schema_allowed)
    results = []
    started = time.monotonic()
    # Authenticate as the limited login. SET/RESET ROLE can never recover the
    # application's privileged identity on this connection.
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        conn.execute("SELECT set_config('search_path','pg_catalog,calliope,calliope_apps',true)")
        conn.execute("SELECT set_config('lock_timeout','5000',true)")
        conn.execute("SELECT set_config('idle_in_transaction_session_timeout','15000',true)")
        conn.execute("SELECT set_config('work_mem','32MB',true)")
        for index, statement in enumerate(batch["statements"]):
            remaining = timeout_ms - int((time.monotonic() - started) * 1000)
            if remaining <= 0:
                raise TimeoutError("Workspace batch exceeded its execution deadline; rolled back")
            conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(remaining),))
            try:
                executed_sql = _execution_sql(statement, batch.get("operators", []), batch.get("operator_adapters"))
                cur = conn.execute(executed_sql)
                rows = cur.fetchmany(ROW_CAP) if cur.description else []
                truncated = bool(cur.fetchone()) if cur.description else False
                results.append({"statement": index + 1, "status": cur.statusmessage,
                                "rows_affected": cur.rowcount if cur.rowcount >= 0 else None,
                                "columns": [c.name for c in (cur.description or [])],
                                "rows": rows, "truncated": truncated})
                if executed_sql != statement:
                    results[-1]["executed_sql"] = executed_sql
            except Exception as exc:
                raise RuntimeError(f"Statement {index + 1} failed; PostgreSQL batch rolled back: {exc}") from None
        try:
            conn.commit()
        except psycopg.OperationalError:
            raise RuntimeError("Connection lost while committing; outcome is unknown. Inspect workspace output before retrying this batch.") from None
    written_schemas = sorted({schema for schema, _ in batch["writes"]})
    return {"committed": True, "database_user": role,
            "schema": written_schemas[0] if len(written_schemas) == 1 else None, "schemas": written_schemas,
            "statements": results, "objects_written": [f"{s}.{t}" for s, t in batch["writes"]],
            "elapsed_ms": int((time.monotonic() - started) * 1000)}
