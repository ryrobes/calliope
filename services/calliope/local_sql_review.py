"""Catalog-only checks for frozen local DDL. Never execute a receipt to inspect it."""
import hashlib
import json

import sqlglot
from sqlglot import exp


def target(sql):
    try:
        statements = sqlglot.parse(sql, read='postgres')
        if len(statements) != 1:
            return None
        node = statements[0]
        if not isinstance(node, (exp.Create, exp.Alter, exp.Drop)):
            return None
        kind = str(node.args.get('kind') or '').upper()
        obj = node.this
        signature = None
        if isinstance(obj, exp.Schema):
            obj = obj.this
        if isinstance(obj, exp.UserDefinedFunction):
            types = []
            for arg in obj.expressions:
                # Do not guess at OUT/INOUT modes or custom function syntax.
                if not isinstance(arg, exp.ColumnDef) or not isinstance(arg.args.get('kind'), exp.DataType):
                    return None
                types.append(arg.args['kind'].sql(dialect='postgres'))
            signature = ','.join(types)
            obj = obj.this
        if isinstance(obj, exp.Index):
            table = obj.args.get('table')
            if not isinstance(table, exp.Table) or not table.db or not isinstance(obj.this, exp.Identifier):
                return None
            obj = exp.Table(this=obj.this.copy(), db=table.args['db'].copy())
        if kind not in ('TABLE', 'VIEW', 'INDEX', 'SEQUENCE', 'FUNCTION'):
            return None
        if not isinstance(obj, exp.Table) or not obj.db or obj.catalog:
            return None
        # Bare names depend on search_path; qualified names preserve identity.
        for part in (obj.this, obj.args['db']):
            if not isinstance(part, exp.Identifier):
                return None
            if not part.args.get('quoted'):
                part.set('this', part.name.lower())
        name = obj.sql(dialect='postgres')
        if kind == 'FUNCTION':
            if signature is None:
                return None
            name += '(' + signature + ')'
        return {'kind': kind, 'name': name, 'operation': node.key,
                'replace': bool(node.args.get('replace'))}
    except Exception:
        return None


def catalog_state(conn, obj):
    if obj['kind'] == 'FUNCTION':
        row = conn.execute(
            "SELECT p.oid,p.proowner,pg_get_functiondef(p.oid) AS definition "
            "FROM pg_catalog.pg_proc p WHERE p.oid=pg_catalog.to_regprocedure(%s)",
            (obj['name'],),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT c.oid,c.relkind,c.relowner,c.reloptions,"
            "CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid,true) END AS view_definition,"
            "(SELECT jsonb_agg(jsonb_build_array(a.attnum,a.attname,a.atttypid,a.atttypmod,"
            "a.attnotnull,a.attidentity,a.attgenerated,a.attisdropped,a.attcollation,"
            "pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum) "
            "FROM pg_catalog.pg_attribute a LEFT JOIN pg_catalog.pg_attrdef d "
            "ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE a.attrelid=c.oid AND a.attnum>0) AS columns,"
            "(SELECT jsonb_agg(pg_get_constraintdef(k.oid,true) ORDER BY k.conname) "
            "FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid) AS constraints,"
            "(SELECT jsonb_agg(pg_get_indexdef(i.indexrelid) ORDER BY i.indexrelid) "
            "FROM pg_catalog.pg_index i WHERE i.indrelid=c.oid OR i.indexrelid=c.oid) AS indexes "
            "FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)",
            (obj['name'],),
        ).fetchone()
    if not row:
        return None
    return hashlib.sha256(json.dumps(dict(row), sort_keys=True, default=str).encode()).hexdigest()


def freeze(conn, sql):
    obj = target(sql)
    if not obj:
        return None
    return {'target': obj, 'before': catalog_state(conn, obj)}


def refresh(conn, run, cache=None):
    """Retirement is permanent; catalog outages only disable this projection."""
    if run.get('status') != 'planned' or (run.get('action_snapshot') or {}).get('executor') != 'local_sql':
        return run
    review = (run.get('verification') or {}).get('local_sql_review') or {}
    if review.get('state') == 'retired':
        return run
    sql = (run.get('input_values') or {}).get('sql') or ''
    obj = target(sql)
    if not obj:
        return run
    baseline = (run.get('plan') or {}).get('local_sql_guard')
    try:
        with conn.transaction():
            conn.execute("SELECT set_config('statement_timeout','1500',true)")
            key = (obj['kind'], obj['name'])
            if cache is not None and key in cache:
                current = cache[key]
            else:
                current = catalog_state(conn, obj)
                if cache is not None:
                    cache[key] = current
    except Exception:
        return {**run, 'verification': {**(run.get('verification') or {}), 'local_sql_review': {
            'state': 'unavailable', 'message': 'Could not check the current database target. Reopen this receipt to retry.',
        }}}
    reason = None
    if obj['operation'] == 'create' and current is not None:
        if not obj['replace'] or not baseline or baseline.get('before') is None:
            reason = 'target_exists'
    if obj['operation'] == 'drop' and current is None:
        reason = 'target_absent'
    if not reason and baseline and (baseline.get('target') != obj or baseline.get('before') != current):
        reason = 'target_changed'
    # Old replacement/ALTER requests did not freeze a catalog baseline. Their
    # safety cannot be established by checking that the named object exists.
    if not reason and not baseline and (obj['replace'] or obj['operation'] == 'alter'):
        reason = 'missing_baseline'
    if not reason:
        return run
    message = {
        'target_exists': 'Target already exists. This old request is retired; any further change needs fresh SQL.',
        'target_absent': 'Target is already absent. This old request is retired.',
        'target_changed': 'Target changed after this SQL was frozen. Request a fresh plan for the current schema.',
        'missing_baseline': 'This old request has no schema baseline. Request a fresh plan for the current schema.',
    }[reason]
    row = conn.execute(
        "UPDATE rvbbit.calliope_action_runs SET verification=verification || jsonb_build_object("
        "'local_sql_review',jsonb_build_object('state','retired','reason',%s::text,'message',%s::text,"
        "'target',%s::text,'checked_at',clock_timestamp())) "
        "WHERE id=%s::uuid AND status='planned' "
        "AND NOT (verification ? 'local_sql_review') RETURNING *",
        (reason, message, obj['name'], str(run['id'])),
    ).fetchone()
    if row:
        return dict(row)
    # Another reader or an approval may have won the compare-and-set.
    row = conn.execute('SELECT * FROM rvbbit.calliope_action_runs WHERE id=%s::uuid', (str(run['id']),)).fetchone()
    return dict(row) if row else run
