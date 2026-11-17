"""Bounded, caller-authorized exploration of published Meaning sources.

This reads relations, never executes a proposed metric, generated definition, or
browser-supplied SQL. Live slices are explicitly separate from saved metrics.
"""
from __future__ import annotations

import time

from psycopg import sql

from app_actions import ActionError
import meaning_graph
import cube_shelf

NUMERIC = {'int2', 'int4', 'int8', 'float4', 'float8', 'numeric'}
AGGREGATES = {'sum': 'SUM', 'avg': 'AVG', 'min': 'MIN', 'max': 'MAX'}


def options(body):
    if not isinstance(body, dict) or set(body) - {'reference', 'dimension', 'measure', 'aggregate', 'filter', 'offset', 'spec', 'probe'}:
        raise ValueError('Use a Meaning reference and structured exploration options')
    reference = meaning_graph.reference(body.get('reference'))
    result = {'reference': reference}
    for key in ('dimension', 'measure'):
        value = body.get(key) or ''
        if not isinstance(value, str) or len(value) > 128:
            raise ValueError('Invalid field name')
        result[key] = value
    result['aggregate'] = body.get('aggregate', 'count')
    if result['aggregate'] not in {'count', *AGGREGATES}:
        raise ValueError('Unsupported aggregate')
    if result['aggregate'] != 'count' and not result['measure']:
        raise ValueError('Choose a numeric field for this aggregate')
    value = body.get('filter')
    if isinstance(value, list):
        if len(value) > 8:
            raise ValueError('Too many selected dimensions')
        for term in value:
            if not isinstance(term, dict) or set(term) - {'field', 'value', 'grain'} or 'value' not in term or not isinstance(term.get('field'), str) or len(term['field']) > 128:
                raise ValueError('Invalid selected dimension')
            if term.get('grain') and term['grain'] not in cube_shelf.GRAINS:
                raise ValueError('Invalid date grouping')
            if term['value'] is not None and (not isinstance(term['value'], str) or len(term['value']) > 4000):
                raise ValueError('Invalid selected value')
        result['filter'] = value
        value = None
    if value is not None:
        if not isinstance(value, dict) or set(value) != {'field', 'value'}:
            raise ValueError('Filter requires field and value')
        if not isinstance(value['field'], str) or len(value['field']) > 128:
            raise ValueError('Invalid filter field')
        if value['value'] is not None and (not isinstance(value['value'], str) or len(value['value']) > 4000):
            raise ValueError('Invalid filter value')
    result.setdefault('filter', value)
    if 'spec' in body:
        spec = body['spec']
        if not isinstance(spec, dict) or set(spec) - {'groupBy', 'measures', 'pivot', 'filters', 'having', 'orderBy', 'limit'}:
            raise ValueError('Use the structured query shelves')
        result['spec'] = spec
    if 'probe' in body:
        probe = body['probe']
        if not isinstance(probe, dict) or set(probe) - {'field', 'search'} or not isinstance(probe.get('field'), str) or not isinstance(probe.get('search', ''), str) or len(probe.get('search', '')) > 200:
            raise ValueError('Invalid field value search')
        if 'spec' not in result:
            raise ValueError('A value search needs query shelves')
        result['probe'] = probe
    if isinstance(result['filter'], list) and 'spec' not in result:
        raise ValueError('Multiple selected dimensions need query shelves')
    offset = body.get('offset', 0)
    if type(offset) is not int or not 0 <= offset <= 10000:
        raise ValueError('Record offset must be 0..10000')
    result['offset'] = offset
    return result


def explore(server, actor, body):
    request = options(body)
    resolved = meaning_graph.resolve(server, actor, request['reference'])
    node = resolved['node']
    if node['kind'] not in ('relation', 'cube'):
        raise ValueError('Explore a source or cube, not a metric definition')
    role = server._session_pg_role(actor)
    with server._conn(read_only=True, role=role) as conn:
        conn.execute("SET LOCAL statement_timeout='5s'")
        conn.execute("SET LOCAL lock_timeout='1s'")
        relation = conn.execute(
            'SELECT n.nspname,c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace '
            'WHERE c.oid=to_regclass(%s) AND has_table_privilege(c.oid,\'SELECT\')', (node['source'],)).fetchone()
        if not relation:
            raise ActionError('NOT_FOUND', 'This data source is unavailable', 404)
        fields = conn.execute(
            'SELECT a.attname AS name,t.typname AS type,format_type(a.atttypid,a.atttypmod) AS display_type '
            'FROM pg_attribute a JOIN pg_type t ON t.oid=a.atttypid '
            'WHERE a.attrelid=%s AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum LIMIT 65',
            (relation['oid'],)).fetchall()
        more_fields = len(fields) > 64
        fields = fields[:64]
        by_name = {f['name']: f for f in fields}
        for key in ('dimension', 'measure'):
            if request[key] and request[key] not in by_name:
                raise ValueError('Field is not available in this source')
        if request['aggregate'] != 'count' and by_name[request['measure']]['type'] not in NUMERIC:
            raise ValueError('This measure must be a numeric field')
        for f in fields:
            f['numeric'] = f['type'] in NUMERIC
        source = sql.Identifier(relation['nspname'], relation['relname'])
        if 'spec' in request:
            return explore_shelves(server, actor, conn, node, fields, more_fields, source, request)
        where, params = sql.SQL(''), []
        if request['filter']:
            field, value = request['filter']['field'], request['filter']['value']
            if field not in by_name:
                raise ValueError('Filter field is not available in this source')
            where = sql.SQL(' WHERE {}::text IS NOT DISTINCT FROM %s').format(sql.Identifier(field))
            params.append(value)
        # Text conversion preserves decimal/bigint precision across JSON. Cap
        # individual cells; the UI states that this is a bounded preview.
        selections = sql.SQL(',').join(sql.SQL('left({}::text,4000) AS {}').format(sql.Identifier(f['name']), sql.Identifier(f['name'])) for f in fields)
        order = sql.SQL(',').join(sql.SQL('{}::text NULLS LAST').format(sql.Identifier(f['name'])) for f in fields)
        rows = conn.execute(sql.SQL('SELECT {} FROM {}{} ORDER BY {} LIMIT 41 OFFSET %s').format(
            selections, source, where, order), [*params, request['offset']]).fetchall()
        result = {'source': node['source'], 'reference': request['reference'], 'fields': fields,
                  'fields_more': more_fields, 'rows': rows[:40], 'has_more': len(rows) > 40,
                  'offset': request['offset'], 'page_size': 40, 'cell_limit': 4000,
                  'as_of': meaning_graph.stamp(), 'scope': 'live source query', 'filter': request['filter']}
        if request['dimension']:
            dimension = sql.Identifier(request['dimension'])
            aggregate = sql.SQL('COUNT(*)') if request['aggregate'] == 'count' else sql.SQL('{}({})').format(
                sql.SQL(AGGREGATES[request['aggregate']]), sql.Identifier(request['measure']))
            groups = conn.execute(sql.SQL(
                'SELECT {}::text AS key,({})::text AS value,COUNT(*)::text AS records '
                'FROM {} GROUP BY {} ORDER BY ({}) DESC NULLS LAST,{}::text NULLS LAST LIMIT 21'
            ).format(dimension, aggregate, source, dimension, aggregate, dimension)).fetchall()
            result.update(groups=groups[:20], groups_more=len(groups) > 20,
                          dimension=request['dimension'], measure=request['measure'], aggregate=request['aggregate'])
        return result


def cell(value):
    # Keep decimals and bigints exact at the browser boundary.
    if isinstance(value, bool):
        return 'true' if value else 'false'
    return None if value is None else str(value)[:4000]


def explore_shelves(server, actor, conn, node, fields, more_fields, source, request):
    started = time.monotonic()
    definition = {}
    if node['kind'] == 'cube':
        definition = meaning_graph.inspect_object(server, actor, request['reference'])['detail']
        if not definition.get('sql'):
            raise ValueError('Cube rollup metadata is unavailable; inspect its definition first')
    catalog = cube_shelf.metadata({'relation': node['source'], 'title': node.get('title', node['source']),
                                  'columns': [{'name': f['name'], 'type': f['display_type']} for f in fields]}, definition)
    raw = request['spec']
    # Empty Values is useful while arranging fields. Validate the rest of the
    # same spec using a temporary row count, but do not run an aggregate.
    count = {'id': 'row-count', 'alias': 'Record count', 'column': None, 'agg': 'count'}
    query = cube_shelf.Query(catalog, {**raw, 'measures': raw.get('measures') or [count]}, allow_row_count=True)
    query.source = source.as_string()
    if request.get('probe'):
        probe = request['probe']
        rows = conn.execute(query.value_probe(probe['field'], probe.get('search', ''))).fetchall()
        return {'values': [cell(r['value']) for r in rows[:200]], 'truncated': len(rows) > 200}
    rollup = None
    if raw.get('measures'):
        values = None
        if query.pivot:
            values = [cell(r['pivot_value']) for r in conn.execute(query.probe()).fetchall()]
        compiled = query.compile(values)
        cursor = conn.execute('SELECT * FROM (' + compiled + ') AS meaning_rollup LIMIT 201')
        rows = cursor.fetchall()
        columns = [c.name for c in cursor.description]
        rollup = {'columns': columns, 'column_types': [{'name': c.name, 'oid': c.type_code} for c in cursor.description], 'rows': [{k: cell(v) for k, v in r.items()} for r in rows[:200]],
                  'has_more': len(rows) > 200, 'limit': 200, 'spec': query.spec}
    # Row selection drills into the group without changing the shelf's totals.
    # Date groups use the same date_trunc expression as the aggregate query.
    # Compare typed values: datetime/boolean driver text can differ from PG's
    # display text while representing exactly the same value.
    selection = request['filter'] or []
    if isinstance(selection, dict):
        selection = [selection]
    where, params = query.where, []
    for term in selection:
        name = term['field']
        if name not in query.fields:
            raise ValueError('Filter field is not available in this source')
        expr = query.dimension({'column': {'name': name}, 'grain': term.get('grain')})
        where += (' AND ' if where else ' WHERE ') + expr + ' IS NOT DISTINCT FROM %s'
        params.append(term['value'])
    selections = sql.SQL(',').join(sql.SQL('left({}::text,4000) AS {}').format(sql.Identifier(f['name']), sql.Identifier(f['name'])) for f in fields)
    order = sql.SQL(',').join(sql.SQL('{}::text NULLS LAST').format(sql.Identifier(f['name'])) for f in fields)
    rows = conn.execute(sql.SQL('SELECT {} FROM {}{} ORDER BY {} LIMIT 41 OFFSET %s').format(
        selections, source, sql.SQL(where), order), [*params, request['offset']]).fetchall()
    return {'source': node['source'], 'reference': request['reference'], 'fields': fields,
            'fields_more': more_fields, 'rows': rows[:40], 'has_more': len(rows) > 40,
            'offset': request['offset'], 'page_size': 40, 'cell_limit': 4000,
            'as_of': meaning_graph.stamp(), 'scope': 'live source query', 'filter': request['filter'],
            'catalog': catalog, 'rollup': rollup, 'elapsed_ms': round((time.monotonic() - started) * 1000)}
