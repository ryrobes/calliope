"""Durable SQL result handles, available before the creating tool returns."""
import uuid


def publish(conn_factory, destination, tool, arguments, result):
    import calliope
    if not destination or not isinstance(result, dict) or result.get('error'):
        return result
    # A server-minted execution key survives transcript compression and the
    # separate live/final projection paths. Never derive identity from SQL alone.
    call_id = 'stage-query:' + uuid.uuid4().hex
    projected = calliope._project_progressive_sql_result(tool, result, arguments, call_id)
    rows = calliope._insert_surfaces(conn_factory, destination['session_id'], destination['turn_id'], projected)
    if not rows:
        if result.get('result_mode') == 'summary':
            result['stage_reference_note'] = 'Summary previews are validation only. Use result_mode="full" for saved query surfaces and board references.'
        return result
    result['_stage_call_id'] = call_id
    result['stage_refs'] = [descriptor(row) for row in rows]
    if tool == 'run_sql_multi':
        for row in rows:
            name = (row.get('source', {}).get('args') or {}).get('query_name')
            if name in result.get('results', {}):
                result['results'][name]['surface_id'] = row['id']
    else:
        result['surface_id'] = rows[0]['id']
    return result


def descriptor(row):
    return {'surface_id': str(row['id']), 'title': row['title'], 'kind': row['kind'],
            'query_name': (row.get('source', {}).get('args') or {}).get('query_name'),
            'turn_id': str(row['turn_id']) if row.get('turn_id') else None}


def lookup(conn_factory, session_id, owner, query='', limit=30):
    """Current chat only, including specialist receipts; no SQL execution."""
    limit = max(1, min(100, int(limit)))
    with conn_factory() as conn:
        rows = conn.execute(
            "SELECT f.* FROM rvbbit.calliope_surfaces f "
            "WHERE f.session_id=%s::uuid AND rvbbit.calliope_session_can_view(f.session_id,%s,false) "
            "AND (%s='' OR position(lower(%s) in lower(f.title || ' ' || coalesce(f.source#>>'{args,query_name}','')))>0) "
            "ORDER BY f.created_at DESC,f.ordinal DESC,f.id DESC LIMIT %s",
            (session_id, owner, query, query, limit),
        ).fetchall()
    return {'session_id': session_id, 'surfaces': [descriptor(r) for r in rows],
            'message': 'Use exact surface_id values in board cells. No queries were executed.'}
