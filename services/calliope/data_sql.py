"""SQL workspace metadata and validation; no independent execution credentials."""
from collections import defaultdict

import sqlglot
from sqlglot.optimizer.scope import traverse_scope

from data_explorer import guard_sql


def operators(factory, role):
    # Only published operators with callable wrappers. Do not expose prompts,
    # credentials, or arbitrary backend configuration in completion metadata.
    with factory(read_only=True, role=role) as conn:
        rows = conn.execute("""SELECT o.name,o.shape,o.arg_names,o.arg_types,o.return_type,
          o.description,o.model,p.oid,pg_get_function_arguments(p.oid) AS args,
          pg_get_function_result(p.oid) AS result,p.pronargs-p.pronargdefaults AS required_count,
          to_jsonb(o)->'steps' AS steps
          FROM rvbbit.operators o JOIN pg_namespace n ON n.nspname='rvbbit'
          JOIN pg_proc p ON p.pronamespace=n.oid AND p.proname=o.name
          WHERE coalesce(to_jsonb(o)->>'visibility','public')<>'kit'
            AND has_schema_privilege(n.oid,'USAGE') AND has_function_privilege(p.oid,'EXECUTE')
          ORDER BY o.name,p.pronargs DESC""").fetchall()
    return [dict(row) for row in rows]


def catalog(factory, role, items, ops):
    allowed = {i['relation']: i for i in items}
    columns = defaultdict(list)
    with factory(read_only=True, role=role) as conn:
        for row in conn.execute("""SELECT n.nspname||'.'||c.relname AS relation,
          a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
          col_description(c.oid,a.attnum) AS description
          FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
          JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname||'.'||c.relname=ANY(%s) AND a.attnum>0 AND NOT a.attisdropped
          ORDER BY n.nspname,c.relname,a.attnum""", (list(allowed),)).fetchall():
            columns[row['relation']].append({k: row[k] for k in ('name','type','description')})
    functions, semantic, seen = [], [], set()
    for op in ops:
        functions.append({'schema':'rvbbit','name':op['name'],'kind':'function','args':op['args'],
                          'result':op['result'],'requiredCount':op['required_count'],
                          'argNames':op['arg_names'],'comment':op['description'],'shape':op['shape']})
        if op['name'] in seen:
            continue
        seen.add(op['name'])
        semantic.append({'name':op['name'],'shape':op['shape'],'argNames':op['arg_names'],
                         'argTypes':op['arg_types'],'returnType':op['return_type'],
                         'description':op['description'],'model':op['model'],
                         'steps':[{k:s[k] for k in ('name','kind','model') if k in s}
                                  for s in (op.get('steps') or []) if isinstance(s,dict)]})
    return {'datasets':[dict(i,columns=columns[i['relation']]) for i in items],
            'functions':functions,'operators':semantic}


def prepare(statement, available, ops):
    """The SQL branch can use all currently authorized datasets, not just a
    novice exploration's selected list. Keep its statement/function boundaries.
    """
    statement = guard_sql(statement, available, operators={o['name'] for o in ops})
    refs = set()
    for scope in traverse_scope(sqlglot.parse_one(statement, read='postgres')):
        for _, table in scope.selected_sources.values():
            if isinstance(table,sqlglot.exp.Table) and isinstance(table.this,sqlglot.exp.Identifier):
                refs.add(f'{table.db or "public"}.{table.name}')
    return {'sql':statement,'sources':sorted(refs)}


SYSTEM_RELATIONS = frozenset({
    'clover_telemetry.calls', 'clover_telemetry.upstream_attempts',
    'rvbbit.mcp_servers', 'rvbbit.mcp_tools', 'rvbbit.mcp_invocations',
    'rvbbit.operator_test_runs', 'rvbbit.operator_test_observations',
})


def system_relations(factory, role, owner, statement):
    """Admin drill-downs may name these internal records; execution keeps its role.

    Normal Data discovery still excludes internal catalogs. This adds only named,
    existing, SELECT-authorized relations, and does not grant database privileges.
    """
    if not isinstance(statement, str):
        return {}
    try:
        refs = {f'{t.db or "public"}.{t.name}' for t in sqlglot.parse_one(statement, read='postgres').find_all(sqlglot.exp.Table)} & SYSTEM_RELATIONS
    except Exception:
        return {}  # prepare() owns syntax diagnostics
    if not refs:
        return {}
    import application_teams
    with factory(read_only=True) as conn:
        if not application_teams._is_admin(conn, owner):
            raise PermissionError('System records are available only to Calliope administrators.')
    with factory(read_only=True, role=role) as conn:
        rows = conn.execute("""SELECT n.nspname AS schema,c.relname AS name,n.nspname||'.'||c.relname AS relation
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname||'.'||c.relname=ANY(%s) AND has_schema_privilege(n.oid,'USAGE')
          AND has_table_privilege(c.oid,'SELECT')""", (sorted(refs),)).fetchall()
    return {r['relation']: dict(r) for r in rows}
