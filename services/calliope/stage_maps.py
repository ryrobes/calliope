"""Declarative SQL-backed Mapbox Stage surfaces. Persist specs, never result rows."""
import json
import math
import re
from functools import partial

import stage_charts

begin_render = partial(stage_charts.begin_render, kind='map')
record_render = partial(stage_charts.record_render, kind='map')
cancel_feedback = partial(stage_charts.cancel_feedback, kind='map')


def number(value, low, high):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and low <= value <= high


def validate(title, queries, sources, layers, viewport=None, size='wide', height=420, row_limit=5000):
    # Reuse the chart contract's title/query/limit checks without introducing a
    # second interpretation of dataset names and SQL limits.
    stage_charts.validate(title, queries, {'layer': [{'data': {'name': name}, 'mark': 'point'} for name in queries]} if isinstance(queries, dict) else {}, size, row_limit)
    if not number(height, 240, 900):
        raise ValueError('height must be 240–900 pixels (default 420)')
    if not isinstance(sources, dict) or not 1 <= len(sources) <= 8:
        raise ValueError('sources must map 1–8 names to SQL geometry bindings')
    datasets = set()
    for name, source in sources.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name):
            raise ValueError('Source names must be identifiers of at most 64 characters')
        allowed = {'dataset', 'longitude', 'latitude', 'geometry', 'tooltip', 'cluster', 'clusterRadius', 'clusterMaxZoom'}
        if not isinstance(source, dict) or set(source) - allowed:
            raise ValueError(f'{name}: use dataset, longitude/latitude or geometry, tooltip and optional clustering settings')
        if source.get('dataset') not in queries:
            raise ValueError(f'{name}: dataset must name a saved query')
        datasets.add(source['dataset'])
        columns = ['geometry'] if 'geometry' in source else ['longitude', 'latitude']
        if 'geometry' in source and any(k in source for k in ('longitude', 'latitude')):
            raise ValueError(f'{name}: choose coordinates OR a GeoJSON geometry column')
        if not all(isinstance(source.get(k), str) and 0 < len(source[k]) <= 128 for k in columns):
            raise ValueError(f'{name}: supply longitude/latitude columns or a GeoJSON geometry column')
        tooltip = source.get('tooltip', [])
        if not isinstance(tooltip, list) or len(tooltip) > 12 or any(not isinstance(k, str) or not 0 < len(k) <= 128 for k in tooltip):
            raise ValueError('tooltip must list up to 12 property names; no HTML templates')
        if 'cluster' in source and not isinstance(source['cluster'], bool):
            raise ValueError('cluster must be a boolean')
        for key, low, high in [('clusterRadius', 1, 200), ('clusterMaxZoom', 0, 22)]:
            if key in source and not number(source[key], low, high):
                raise ValueError(f'{key} must be {low}–{high}')
        if source.get('cluster') and 'geometry' in source:
            raise ValueError('Clustering requires longitude/latitude point columns')
    if datasets != set(queries):
        raise ValueError('Every query must be used by a source')
    if not isinstance(layers, list) or not 1 <= len(layers) <= 24:
        raise ValueError('layers must contain 1–24 native Mapbox layer objects')
    ids = set()
    for layer in layers:
        if not isinstance(layer, dict) or set(layer) - {'id', 'type', 'source', 'paint', 'layout', 'filter', 'minzoom', 'maxzoom'}:
            raise ValueError('Layers support native id, type, source, paint, layout, filter, minzoom and maxzoom')
        lid = layer.get('id')
        if not isinstance(lid, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,63}', lid) or lid in ids:
            raise ValueError('Layer IDs must be unique identifiers')
        ids.add(lid)
        if layer.get('source') not in sources or layer.get('type') not in {'circle', 'heatmap', 'line', 'fill', 'fill-extrusion', 'symbol'}:
            raise ValueError('Use a named source and circle, heatmap, line, fill, fill-extrusion or symbol layers')
        for prop in ('paint', 'layout'):
            if prop in layer and not isinstance(layer[prop], dict):
                raise ValueError(f'{prop} must be a native Mapbox object')
        for prop in ('minzoom', 'maxzoom'):
            if prop in layer and not number(layer[prop], 0, 24):
                raise ValueError(f'{prop} must be 0–24')
    viewport = {'fit': 'data'} if viewport is None else viewport
    if not isinstance(viewport, dict) or set(viewport) - {'fit', 'center', 'zoom', 'pitch', 'bearing'}:
        raise ValueError('viewport supports fit:data or center:[lon,lat], zoom, pitch and bearing')
    if 'fit' in viewport and viewport['fit'] != 'data':
        raise ValueError('fit must be data')
    if 'center' in viewport and not position(viewport['center']):
        raise ValueError('center must be [longitude, latitude]')
    for key, low, high in [('zoom', 0, 22), ('pitch', 0, 75), ('bearing', -360, 360)]:
        if key in viewport and not number(viewport[key], low, high):
            raise ValueError(f'{key} must be {low}–{high}')
    payload = dict(format='mapbox', queries=queries, sources=sources, layers=layers, viewport=viewport,
                   size=size, height=height, row_limit=row_limit, live=True)
    if len(json.dumps(payload, allow_nan=False).encode()) > 1_000_000:
        raise ValueError('Map specification exceeds 1 MB')
    return payload


def position(value):
    return isinstance(value, (list, tuple)) and 2 <= len(value) <= 3 and number(value[0], -180, 180) and number(value[1], -90, 90) and (len(value) == 2 or number(value[2], -1e8, 1e8))


def geometry(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return None
    if not isinstance(value, dict):
        return None
    kind, coords = value.get('type'), value.get('coordinates')
    def line(c):
        return isinstance(c, list) and len(c) >= 2 and all(position(p) for p in c)
    def polygon(c):
        return isinstance(c, list) and bool(c) and all(line(r) and len(r) >= 4 and r[0] == r[-1] for r in c)
    valid = position(coords) if kind == 'Point' else (
        isinstance(coords, list) and bool(coords) and all(position(p) for p in coords) if kind == 'MultiPoint' else
        line(coords) if kind == 'LineString' else polygon(coords) if kind == 'Polygon' else
        isinstance(coords, list) and bool(coords) and all(line(c) for c in coords) if kind == 'MultiLineString' else
        isinstance(coords, list) and bool(coords) and all(polygon(c) for c in coords) if kind == 'MultiPolygon' else False)
    return {'type': kind, 'coordinates': coords} if valid else None


def resolve_sources(payload, datasets):
    resolved, stats = {}, {}
    for name, source in payload['sources'].items():
        features, invalid = [], 0
        rows = datasets[source['dataset']]
        for index, row in enumerate(rows):
            if 'geometry' in source:
                g = geometry(row.get(source['geometry']))
            else:
                # SQL numeric text is allowed; null/blank/bool must never become 0,0.
                xy = [row.get(source['longitude']), row.get(source['latitude'])]
                try:
                    xy = [float(v) if v is not None and not isinstance(v, bool) and str(v).strip() else None for v in xy]
                except (ValueError, TypeError):
                    xy = []
                g = {'type': 'Point', 'coordinates': xy} if position(xy) else None
            if not g:
                invalid += 1
                continue
            properties = {k: v for k, v in row.items() if k != source.get('geometry')}
            features.append({'type': 'Feature', 'id': index, 'geometry': g, 'properties': properties})
        if rows and not features:
            raise ValueError(f'{name}: none of {len(rows)} rows has valid geometry; check coordinate/GeoJSON columns')
        resolved[name] = {'type': 'FeatureCollection', 'features': features}
        stats[name] = {'features': len(features), 'invalid_geometry_rows': invalid}
    return resolved, stats


def publish(conn_factory, destination, title, payload, replaces_surface_id=None):
    import calliope
    session, turn = destination['session_id'], destination['turn_id']
    digest = stage_charts.fingerprint(title, payload)
    parent = calliope._uuid(replaces_surface_id) if replaces_surface_id else None
    if replaces_surface_id and not parent:
        raise ValueError('replaces_surface_id must be a map surface UUID')
    with conn_factory() as conn, conn.transaction():
        conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('stage-map:' + session,))
        lineage = 'live-map:' + digest
        if parent:
            row = conn.execute("SELECT lineage_key FROM rvbbit.calliope_surfaces WHERE id=%s AND session_id=%s AND kind='map'", (parent, session)).fetchone()
            if not row:
                raise ValueError('Replacement map must belong to this chat')
            lineage = row['lineage_key']
        latest = conn.execute("SELECT * FROM rvbbit.calliope_surfaces WHERE session_id=%s AND lineage_key=%s AND kind='map' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 1", (session, lineage)).fetchone()
        identical = latest and latest['payload'].get('content_sha256') == digest
        if parent and latest and str(latest['id']) != parent and not (identical and str(latest.get('parent_surface_id')) == parent):
            raise ValueError(f"Map was revised; read_map and update latest surface {latest['id']}")
        if identical:
            return {'surface_id': str(latest['id']), 'kind': 'map', 'reused': True}
        payload = {**payload, 'content_sha256': digest}
        parent = str(latest['id']) if latest else None
        rows = calliope._insert_surfaces(conn_factory, session, turn, [{
            'kind': 'map', 'title': title.strip(), 'lineage_key': lineage, 'parent_surface_id': parent,
            'tool_name': 'publish_map', 'tool_call_id': f'map:{turn}:{parent or "root"}:{digest}',
            'payload': payload, 'source': {'origin': 'publish_map'},
            'presentation': {'purpose': 'map', 'phase': 'published', 'size': payload['size']}}])
        if latest:
            cancel_feedback(conn, session, [latest['id']])
        return {'surface_id': rows[0]['id'], 'kind': 'map', 'reused': False, 'size': payload['size'],
                'message': 'Published. Viewer-scoped SQL runs on open/refresh. Use read_map for render feedback and update_map for revisions.'}


def read(conn_factory, surface_id, viewer):
    import calliope
    sid = calliope._uuid(surface_id)
    if not sid:
        return {'error': {'code': 'NOT_FOUND', 'message': 'Map not found'}}
    with conn_factory() as conn:
        row = conn.execute("SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s AND kind='map' AND rvbbit.calliope_session_can_view(session_id,%s,false)", (sid, viewer)).fetchone()
        if not row:
            return {'error': {'code': 'NOT_FOUND', 'message': 'Map not found'}}
        revisions = conn.execute("SELECT id,title FROM rvbbit.calliope_surfaces WHERE session_id=%s AND lineage_key=%s AND kind='map' ORDER BY created_at DESC,ordinal DESC,id DESC LIMIT 30", (row['session_id'], row['lineage_key'])).fetchall()
    return {'surface_id': str(row['id']), 'title': row['title'], 'latest_surface_id': str(revisions[0]['id']),
            'revisions': [{'surface_id': str(r['id']), 'title': r['title']} for r in revisions],
            **{k: v for k, v in row['payload'].items() if k not in {'render_attempt', 'render_feedback_id'}}}
