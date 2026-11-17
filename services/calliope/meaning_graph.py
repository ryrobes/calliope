"""A bounded, read-only graph over published Meaning and authorized artifacts.

The graph is a projection, not a second ontology. Pins store references only.
Opening the map never runs generated SQL, models, or metric materialization.
"""
from __future__ import annotations

import asyncio
import contextvars
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
import re
import threading
import uuid

from app_actions import ActionError
from semantic_contracts import metric_review

LOG = logging.getLogger(__name__)
DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.workspace_object_pins (
 id uuid PRIMARY KEY, actor text NOT NULL, reference jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(actor,reference)
);
REVOKE ALL ON rvbbit.workspace_object_pins FROM PUBLIC;
"""
_lock, _ready = threading.Lock(), set()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def bounded(value, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise ValueError('Expected a bounded object identifier')
    return value


def reference(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'area', 'node'} or value['kind'] != 'meaning':
        raise ValueError('Meaning references use kind, area and node')
    bounded(value['area'])
    bounded(value['node'], 700)
    if value['node'].split(':', 1)[0] not in ('area', 'relation', 'metric', 'idea', 'artifact', 'breakdown', 'cube'):
        raise ValueError('Unknown Meaning object kind')
    return dict(value)


def ref(area, node):
    return {'kind': 'meaning', 'area': area, 'node': node}


def stable(kind, area, title):
    return kind + ':' + hashlib.sha256((area + '\0' + title.casefold()).encode()).hexdigest()[:24]


def array(value):
    return value if isinstance(value, list) else []


def counted(n, noun):
    return f"{n} {noun}" + ('' if n == 1 else 's')


def area_node(d):
    key = d['domain_key']
    return {'id': 'area:' + key, 'kind': 'area', 'title': d['name'], 'description': d.get('about') or '',
            'status': 'provisional', 'basis': 'Published Meaning dossier', 'area': key,
            'reference': ref(key, 'area:' + key), 'updated_at': str(d.get('updated_at') or ''),
            'subtitle': f"{counted(len(array(d.get('members'))), 'source')} · {counted(len(array(d.get('candidate_metrics'))), 'metric idea')}",
            'questions': array(d.get('questions'))[:6], 'gaps': array(d.get('gaps'))[:6],
            'groups': [{'id': stable('group', key, g['title']), 'title': g['title'], 'size': len(g['tables'])} for g in dossier_groups(d)]}


def dossier_groups(d):
    """The dossier's named groupings of member tables, in published order."""
    out, seen = [], set()
    for g in array(d.get('areas')):
        title = str(g.get('name') or '').strip() if isinstance(g, dict) else ''
        tables = [t for t in array(g.get('tables')) if isinstance(t, str) and t not in seen] if title else []
        seen.update(tables)
        if tables:
            out.append({'title': title, 'description': str(g.get('about') or ''), 'tables': tables})
    return out


def schema_of(relation):
    return relation.rsplit('.', 1)[0] if '.' in relation else ''


def area_ties(d, others, links, local=None):
    """Other published areas this one touches, strongest evidence first.

    A tie is a member both areas publish, or a probed join from one of this
    area's objects to another area's member. A common schema is the weakest
    tie: it is offered only when nothing stronger exists and the schema is not
    shared so widely that it says nothing.
    """
    members = set(array(d.get('members')))
    local = set(local) if local is not None else members
    reach = {}
    for o in [d, *others]:
        for s in {schema_of(r) for r in array(o.get('members'))} - {''}:
            reach.setdefault(s, set()).add(o['domain_key'])
    out = []
    for o in others:
        theirs = set(array(o.get('members')))
        ties = [{'local': 'relation:' + r, 'remote': r, 'label': 'shares this source', 'state': 'shared',
                 'basis': 'A member of both published areas'} for r in sorted(members & theirs)]
        for link in links:
            if link.get('state') == 'contradicted':
                continue
            ends = ((link['from_relation'], link['to_relation'], link.get('from_column'), link.get('to_column')),
                    (link['to_relation'], link['from_relation'], link.get('to_column'), link.get('from_column')))
            for here, there, a, b in ends:
                if here in local and there in theirs and there not in local:
                    ties.append({'local': 'relation:' + here, 'remote': there, 'state': link.get('state') or 'unverified',
                                 'label': 'joins on ' + (a if a == b else f'{a} → {b}'),
                                 'basis': link.get('basis') or 'Probed join', 'match_rate': link.get('match_rate')})
        strong = len(ties)
        if not ties:
            common = sorted(s for s in {schema_of(r) for r in members} & {schema_of(r) for r in theirs} if s and len(reach[s]) <= 3)
            ties = [{'local': None, 'remote': None, 'label': 'same schema ' + s, 'state': 'association',
                     'basis': 'Members come from the same database schema; no record-level link is established'} for s in common]
        if ties:
            key = o['domain_key']
            out.append({'id': 'area:' + key, 'kind': 'area', 'area': key, 'title': o['name'], 'description': o.get('about') or '',
                        'subtitle': f"{len(theirs)} sources", 'reference': ref(key, 'area:' + key), 'ties': ties, 'strength': strong})
    return sorted(out, key=lambda n: (-n['strength'], n['title']))


def landscape_links(domains, links):
    """One edge per pair of related areas for the all-areas map."""
    edges = []
    for i, d in enumerate(domains):
        for n in area_ties(d, domains[i + 1:], links):
            ties = n['ties']
            joined = [t for t in ties if t['state'] != 'association']
            edges.append({'id': stable('edge', 'area:' + d['domain_key'], n['id']), 'from': 'area:' + d['domain_key'], 'to': n['id'],
                          'label': ties[0]['label'] if len(ties) == 1 else f'{len(ties)} links',
                          'state': 'association' if not joined else 'shared' if all(t['state'] == 'shared' for t in ties) else ties[0]['state'],
                          'evidence': {'basis': 'Shared members and probed joins between published areas', 'ties': ties[:12]}})
    return edges


def permitted_areas(server, conn, actor):
    rows = conn.execute("SELECT domain_key,name,about,members,core,roles,areas,questions,gaps,candidate_metrics,candidate_breakdowns,updated_at "
                        "FROM rvbbit.meaning_published_domains WHERE origin='map' AND status='done' ORDER BY name,domain_key LIMIT 201").fetchall()
    role = server._session_pg_role(actor)
    if role:
        # Dossier prose can describe every member. Do not expose a partial
        # dossier when a Burrow caller cannot read all of its constituent data.
        members = sorted({r for d in rows for r in array(d.get('members'))})
        allowed = {r['relation'] for r in conn.execute(
            "SELECT relation FROM unnest(%s::text[]) relation JOIN pg_class c ON c.oid=to_regclass(relation) "
            "WHERE NOT c.relrowsecurity AND has_table_privilege(%s,c.oid,'SELECT')", (members, role)).fetchall()}
        rows = [d for d in rows if set(array(d.get('members'))) <= allowed]
    return rows, role


def metric_presentation(metric):
    """Display diversity only: never merge identities or change a metric definition."""
    labels = metric.get('labels') or {}
    formula = str(labels.get('formula') or labels.get('scalar_formula') or '').strip()
    # Restrict this hint to an exact scalar COUNT expression. Conditional and
    # distinct business counts must not be mistaken for a generic row count.
    kind = 'count' if re.fullmatch(r'count\s*\(\s*(?:\*|1)\s*\)', formula, re.I) else 'measure'
    group = stable('formula', ','.join(sorted(array(labels.get('source_objects')))), formula) if formula else 'metric:' + metric['name']
    return kind, group


def metric_order(metric, core):
    labels = metric.get('labels') or {}
    return (labels.get('status') == 'implied', metric_presentation(metric)[0] == 'count',
            not bool(set(array(labels.get('source_objects'))) & set(core)), metric['name'])


def project(d, relations, metrics, artifacts, links, *, cubes=(), limit=12, focus='', role=None, neighbors=()):
    """Pure projection: explicit edges, stable IDs, no title-similarity guesses."""
    area = d['domain_key']
    root = area_node(d)
    nodes, edges = {root['id']: root}, []
    def add(node):
        node.update(area=area, reference=ref(area, node['id']))
        nodes[node['id']] = node
    def edge(a, b, label, state, evidence):
        if a in nodes and b in nodes:
            edges.append({'id': stable('edge', a, b + label + str(evidence)), 'from': a, 'to': b,
                          'label': label, 'state': state, 'evidence': evidence})
    core = array(d.get('core'))
    relations = sorted(relations, key=lambda r: (r['relation'] not in core,
                       core.index(r['relation']) if r['relation'] in core else 0, r['relation']))
    # Load the complete authorized area; visual grouping belongs to the client.
    def chosen(items, key):
        return sorted(items, key=lambda item: key(item) != focus)
    for r in chosen(relations, lambda r: 'relation:' + r['relation']):
        node = {'id': 'relation:' + r['relation'], 'kind': 'relation',
                'title': r.get('business_name') or r['relation'].split('.')[-1].replace('_', ' '),
                'description': r.get('what') or '', 'subtitle': r['relation'], 'source': r['relation'],
                'status': 'provisional', 'basis': r.get('placed_by') or 'Published area membership',
                'updated_at': str(r.get('updated_at') or ''), 'confidence': r.get('confidence'),
                'facets': array(r.get('facets'))[:5],
                # What Meaning inferred the table to be, not just where it sits.
                'object_role': r.get('object_role') or '', 'subject_role': r.get('subject_role') or '',
                'relation_kind': r.get('relation_kind') or '', 'grain': r.get('narrative') or '',
                'time_grain': r.get('time_grain') or '', 'entity_keys': array(r.get('entity_keys'))[:4]}
        if not role and r.get('n_rows') is not None:
            node['preview'] = {'value': r['n_rows'], 'label': 'Profiled rows', 'at': node['updated_at']}
        add(node)
        if r['relation'] in array(d.get('members')):
            edge(root['id'], node['id'], 'includes', 'published', {'basis': 'Published dossier membership'})
    cube_refs = {c['relation'] for c in cubes}
    def source_id(source):
        return ('cube:' if source in cube_refs else 'relation:') + source
    for c in cubes:
        sources = array(c.get('sources')) or [r['relation'] for r in array(c.get('derives_from'))]
        # Even a core branch/product dimension can be shared by unrelated facts.
        # Only an explicit subject declaration establishes business membership.
        direct = (c.get('labels') or {}).get('subject') == area
        add({'id': 'cube:' + c['relation'], 'kind': 'cube', 'title': c.get('name') or c['relation'],
             'source': c['relation'], 'cube': c['relation'].removeprefix('cubes.'),
             'description': c.get('description') or c.get('what') or '', 'subtitle': c.get('grain') or 'Grain not recorded',
             'grain': c.get('grain'), 'version': c.get('version'), 'status': 'registered',
             'basis': 'Versioned cube definition', 'sources': sources,
             'dimensions': array(c.get('dimensions')), 'measures': array(c.get('measures')),
             'refresh': {} if role else c.get('refresh') or {},
             'association': 'declared subject' if direct else 'shared source',
             'updated_at': str(c.get('defined_at') or '')})
        edge(root['id'], 'cube:' + c['relation'], 'analyzed through' if direct else 'shares sources with',
             'declared' if direct else 'association', {'basis': 'Cube SQL references area members; shared sources do not establish the same fact population'})
    for c in cubes:
        for source in nodes['cube:' + c['relation']]['sources']:
            edge(source_id(source), 'cube:' + c['relation'], 'feeds', 'declared', {'basis': 'Cube SQL dependency'})
    metrics = sorted(metrics, key=lambda m: metric_order(m, core))
    metric_names = {m['name'].casefold() for m in metrics}
    for m in metrics:
        metric_names.update(str(a).casefold().replace(' ', '_') for a in array((m.get('labels') or {}).get('aliases')))
    for m in chosen(metrics, lambda m: 'metric:' + m['name']):
        labels = m.get('labels') or {}
        status = labels.get('status') or 'registered'
        metric_kind, display_group = metric_presentation(m)
        node = {'id': 'metric:' + m['name'], 'kind': 'metric', 'title': m['name'].replace('_', ' '),
                'metric': m['name'], 'metric_kind': metric_kind, 'display_group': display_group, 'description': m.get('description') or '', 'status': status,
                'subtitle': m.get('grain') or 'Scalar metric', 'basis': 'Versioned metric registry',
                'formula': labels.get('formula') or labels.get('scalar_formula') or '',
                'version': m.get('version'), 'updated_at': str(m.get('created_at') or ''),
                'sources': array(m.get('sources')) or array(labels.get('source_objects')),
                'defaults': m.get('params') or {}, 'contract': labels.get('metric_contract'), 'review': metric_review(m)}
        history = array(m.get('history'))
        if not role and history:
            # Keep observations at the current definition version and scope.
            h = history[0]
            node['preview'] = {'value': h.get('value'), 'label': 'Saved daily snapshot', 'at': str(h.get('observed_at') or '')}
            node['series'] = [h['value'] for h in reversed(history) if type(h.get('value')) in (int, float)]
        elif not role and isinstance(labels.get('probe'), dict) and labels['probe'].get('ok') and 'value' in labels['probe']:
            node['preview'] = {'value': labels['probe']['value'], 'label': 'Registration sample', 'at': labels.get('registered_at') or node['updated_at']}
        add(node)
        metric_names.add(m['name'].casefold())
        metric_names.update(str(a).casefold().replace(' ', '_') for a in array(labels.get('aliases')))
        direct = labels.get('subject') == area
        edge(root['id'], node['id'], 'measured by' if direct else 'shares sources with',
             ('implied' if status == 'implied' else 'declared') if direct else 'association',
             {'basis': 'Declared metric subject' if direct else 'Shared source or cube dependency; not a declared business metric for this area'})
        for source in node['sources']:
            edge(source_id(source), node['id'], 'measured by', 'declared', {'basis': 'Metric SQL / declared dependency', 'source': source})
    ideas = [i for i in array(d.get('candidate_metrics')) if isinstance(i, dict) and str(i.get('name', '')).casefold().replace(' ', '_') not in metric_names]
    for idea in chosen(ideas, lambda i: stable('idea', area, i.get('name', ''))):
        ident = stable('idea', area, idea.get('name', ''))
        add({'id': ident, 'kind': 'idea', 'title': idea.get('name') or 'Metric idea', 'status': 'suggested',
             'description': idea.get('why') or '', 'subtitle': idea.get('grain') or 'Definition not registered',
             'formula': idea.get('formula') or '', 'proposal': idea, 'basis': 'Discovery proposal; not a canonical metric'})
        edge(root['id'], ident, 'suggests', 'unverified', {'basis': 'Published metric idea, not a registered definition'})
    for b in chosen(array(d.get('candidate_breakdowns')), lambda b: stable('breakdown', area, b.get('name', ''))):
        if not isinstance(b, dict):
            continue
        ident = stable('breakdown', area, b.get('name', ''))
        add({'id': ident, 'kind': 'breakdown', 'title': b.get('name') or 'Breakdown', 'status': 'suggested',
             'description': b.get('purpose') or '', 'subtitle': 'Grouped analysis idea', 'basis': 'Published dossier breakdown',
             'dimensions': array(b.get('dimensions')), 'measures': array(b.get('measures')), 'caveat': b.get('caveats') or ''})
        edge(root['id'], ident, 'explore by', 'unverified', {'basis': 'Published breakdown, calculation not executed'})
    for a in chosen(artifacts, lambda a: 'artifact:' + a['slug']):
        ident = 'artifact:' + a['slug']
        add({'id': ident, 'kind': 'artifact', 'title': a['name'], 'description': a.get('description') or '',
             'status': 'published', 'subtitle': (a.get('app_kind') or 'app') + ' · v' + str(a.get('latest_version', '')),
             'basis': 'Latest published artifact dependencies', 'slug': a['slug'], 'runtime': a.get('runtime_kind'),
             'updated_at': str(a.get('updated_at') or ''), 'dependencies': array(a.get('dependencies'))})
        edge(root['id'], ident, 'explored in', 'declared', {'basis': 'Uses a source or metric in this area'})
        for dep in array(a.get('dependencies')):
            key = source_id(dep['object_ref']) if dep['kind'] == 'table' else 'metric:' + dep['object_ref']
            edge(key, ident, 'used by', 'declared', {'basis': dep.get('source') or 'Published dependency', 'version': a.get('latest_version')})
    for link in links:
        edge('relation:' + link['from_relation'], 'relation:' + link['to_relation'],
             'joins to', link['state'], {k: link.get(k) for k in ('basis', 'from_column', 'to_column', 'match_rate', 'sampled', 'confidence')})
    totals = {'cubes': len(cubes), 'sources': len(relations), 'metrics': len(metrics), 'artifacts': len(artifacts),
              'metric_ideas': len(ideas), 'breakdowns': len(array(d.get('candidate_breakdowns')))}
    catalog = []
    for kind, key in [('cube', 'cubes'), ('relation', 'sources'), ('metric', 'metrics'), ('artifact', 'artifacts'),
                      ('idea', 'metric_ideas'), ('breakdown', 'breakdowns')]:
        loaded = sum(n['kind'] == kind for n in nodes.values())
        cap = totals[key]
        catalog.append({'kind': kind, 'loaded': loaded, 'available': totals[key], 'cap': cap,
                        'available_is_lower_bound': False,
                        'can_load': loaded < min(totals[key], cap)})
    # Dossier groupings and neighbouring areas are diagram structure, not
    # objects: they are listed beside the nodes so existing readers ignore them.
    groups = []
    for g in dossier_groups(d):
        ids = [k for t in g['tables'] for k in ('relation:' + t, 'cube:' + t) if k in nodes]
        if ids:
            groups.append({'id': stable('group', area, g['title']), 'title': g['title'], 'description': g['description'],
                           'members': ids, 'basis': 'Published dossier grouping'})
    return {'area': area, 'nodes': list(nodes.values()), 'edges': edges, 'totals': totals, 'catalog': catalog,
            'groups': groups, 'neighbors': [n for n in neighbors if any(t['local'] is None or t['local'] in nodes for t in n['ties'])],
            'more': False, 'complete': True, 'scope': 'Authorized area members and their declared dependencies', 'limit': limit,
            'publication_at': str(d.get('updated_at') or ''), 'as_of': stamp()}


def graph(server, actor, area='', limit=12, focus=''):
    if area:
        bounded(area)
    if focus:
        bounded(focus, 700)
    if type(limit) is not int or not 4 <= limit <= 40:
        raise ValueError('Graph limit must be 4..40 per object type')
    with server._conn(read_only=True) as conn:
        conn.execute("SET LOCAL statement_timeout='8s'")
        domains, role = permitted_areas(server, conn, actor)
        if not area:
            shown = domains[:200]
            everyone = sorted({r for d in shown for r in array(d.get('members'))})
            links = conn.execute("SELECT from_relation,from_column,to_relation,to_column,basis,state,match_rate FROM rvbbit.meaning_links "
                                 "WHERE from_relation=ANY(%s) AND to_relation=ANY(%s) ORDER BY from_relation,to_relation,from_column",
                                 (everyone, everyone)).fetchall() if everyone else []
            return {'area': '', 'nodes': [area_node(d) for d in shown], 'edges': [],
                    'area_links': landscape_links(shown, links),
                    'more': len(domains) > 200, 'limit': 200, 'as_of': stamp()}
        d = next((d for d in domains if d['domain_key'] == area), None)
        if not d:
            raise ActionError('NOT_FOUND', 'This published Meaning area is unavailable', 404)
        members = array(d['members'])
        # Definitions are metadata. SQL references give the actual lineage; a
        # shared dimension does not establish membership of the same fact set.
        all_cubes = conn.execute("SELECT DISTINCT ON(name) name,version,sql,grain,description,created_at,labels,"
            "'cubes.'||name AS relation,rvbbit.sql_relation_refs(sql) AS sources "
            "FROM rvbbit.cube_defs ORDER BY name,version DESC").fetchall()
        all_metrics = conn.execute("SELECT * FROM (SELECT DISTINCT ON(name) name,version,description,grain,params,labels,created_at,"
            "rvbbit.sql_relation_refs(sql) AS sources FROM rvbbit.metric_defs ORDER BY name,version DESC) m "
            "WHERE coalesce(labels->>'status','')<>'retired' ORDER BY name").fetchall()
        for m in all_metrics:
            m['sources'] = array(m.get('sources')) or array((m.get('labels') or {}).get('source_objects'))
        if role:
            refs = sorted(set(members) | {r for c in all_cubes for r in [c['relation'], *array(c['sources'])]}
                          | {r for m in all_metrics for r in m['sources']})
            allowed = {r['relation'] for r in conn.execute(
                "SELECT relation FROM unnest(%s::text[]) relation JOIN pg_class c ON c.oid=to_regclass(relation) "
                "WHERE NOT c.relrowsecurity AND has_table_privilege(%s,c.oid,'SELECT')", (refs, role)).fetchall()}
            all_cubes = [c for c in all_cubes if set([c['relation'], *array(c['sources'])]) <= allowed]
            all_metrics = [m for m in all_metrics if m['sources'] and set(m['sources']) <= allowed]
        related = set(members)
        cube_by_ref = {c['relation']: c for c in all_cubes}
        cubes = [c for c in all_cubes if set(array(c['sources'])) & related]
        # Close both upstream and downstream cube dependencies without guessing
        # connections through unrelated raw dimensions.
        named_metric_sources = {r for m in all_metrics if (m.get('labels') or {}).get('subject') == area for r in m['sources']}
        cube_refs = {c['relation'] for c in cubes} | (named_metric_sources & cube_by_ref.keys())
        while True:
            expanded = cube_refs | {r for ref in cube_refs for r in array(cube_by_ref[ref]['sources']) if r in cube_by_ref}
            expanded |= {c['relation'] for c in all_cubes if set(array(c['sources'])) & cube_refs}
            if expanded == cube_refs:
                break
            cube_refs = expanded
        cubes = [c for c in all_cubes if c['relation'] in cube_refs]
        related |= cube_refs
        metrics = [m for m in all_metrics if (m.get('labels') or {}).get('subject') == area or set(m['sources']) & related]
        relation_names = sorted((set(members) | {r for c in cubes for r in array(c['sources'])}
                                | {r for m in metrics for r in m['sources']}) - cube_refs)
        relations = conn.execute("SELECT names.relation,m.business_name,m.what,m.n_rows,m.confidence,m.updated_at,m.placed_by,"
            "m.kind AS relation_kind,m.object_role,m.subject_role,m.narrative,m.time_grain,m.entity_keys,"
            "(SELECT jsonb_agg(x.col_name) FROM (SELECT col_name FROM rvbbit.meaning_columns c WHERE c.relation=names.relation "
            "AND c.role='category' ORDER BY c.col_name LIMIT 5) x) AS facets "
            "FROM unnest(%s::text[]) names(relation) LEFT JOIN rvbbit.meaning_relations m ON m.relation=names.relation", (relation_names,)).fetchall()
        # Read registered cube metadata, not source rows or generated SQL.
        if cubes:
            lenses = conn.execute("SELECT relation,rvbbit.meaning_lens(relation) AS lens FROM unnest(%s::text[]) relation", (sorted(cube_refs),)).fetchall()
            by_ref = {r['relation']: r['lens'] or {} for r in lenses}
            cubes = [dict(c, **{k: v for k, v in by_ref.get(c['relation'], {}).items() if k not in ('sources', 'description', 'version', 'grain', 'sql')}) for c in cubes]
        for m in metrics:
            m['history'] = []
        if not role and metrics:
            histories = conn.execute("SELECT d.name,h.value,h.observed_at,h.data_as_of FROM unnest(%s::text[]) d(name) "
                "CROSS JOIN LATERAL (SELECT DISTINCT ON ((observed_at AT TIME ZONE 'UTC')::date) value,observed_at,data_as_of "
                "FROM rvbbit.metric_default_observations WHERE metric_name=d.name "
                "ORDER BY (observed_at AT TIME ZONE 'UTC')::date DESC,observed_at DESC,observation_id DESC LIMIT 24) h",
                ([m['name'] for m in metrics],)).fetchall()
            by_name = {m['name']: m for m in metrics}
            for h in histories:
                value = h['value'].get('value') if isinstance(h['value'], dict) else h['value']
                by_name[h['name']]['history'].append({'value': value, 'observed_at': str(h['observed_at']), 'data_as_of': str(h['data_as_of'])})
        metric_names = [m['name'] for m in metrics]
        artifacts = conn.execute("SELECT d.slug,d.name,d.description,d.runtime_kind,d.app_kind,d.latest_version,d.updated_at,"
            "jsonb_agg(DISTINCT jsonb_build_object('kind',p.kind,'object_ref',p.object_ref,'source',p.source)) AS dependencies "
            "FROM rvbbit.dashboards d JOIN rvbbit.dashboard_deps p ON p.dashboard_id=d.id AND p.version=d.latest_version "
            "WHERE NOT d.archived AND rvbbit.artifact_can_view(d.id,%s,false) "
            "AND ((p.kind='table' AND p.object_ref=ANY(%s)) OR (p.kind='metric' AND p.object_ref=ANY(%s))) "
            "GROUP BY d.id ORDER BY d.name,d.slug", (actor, sorted(related), metric_names)).fetchall()
        links = conn.execute("SELECT from_relation,from_column,to_relation,to_column,basis,state,match_rate,sampled,confidence "
            "FROM rvbbit.meaning_links WHERE from_relation=ANY(%s) AND to_relation=ANY(%s) ORDER BY from_relation,to_relation,from_column",
            (relation_names, relation_names)).fetchall()
        others = [o for o in domains if o['domain_key'] != area]
        elsewhere = sorted({r for o in others for r in array(o.get('members'))} - set(relation_names))
        crossing = conn.execute("SELECT from_relation,from_column,to_relation,to_column,basis,state,match_rate FROM rvbbit.meaning_links "
            "WHERE (from_relation=ANY(%s) AND to_relation=ANY(%s)) OR (from_relation=ANY(%s) AND to_relation=ANY(%s)) "
            "ORDER BY from_relation,to_relation,from_column", (relation_names, elsewhere, elsewhere, relation_names)).fetchall() if elsewhere else []
    neighbors = area_ties(d, others, crossing, local=relation_names)
    return project(d, relations, metrics, artifacts, links, cubes=cubes, limit=limit, focus=focus, role=role, neighbors=neighbors)


def resolve(server, actor, value):
    value = reference(value)
    result = graph(server, actor, value['area'], 12, value['node'])
    node = next((n for n in result['nodes'] if n['id'] == value['node']), None)
    if not node:
        raise ActionError('NOT_FOUND', 'This Meaning object is no longer available', 404)
    return {'node': node, 'edges': [e for e in result['edges'] if node['id'] in (e['from'], e['to'])],
            'as_of': result['as_of']}


def inspect_object(server, actor, value):
    """Selected-object metadata, fetched on demand; never evaluate its SQL.

    Resolve first so published membership and artifact/role policy are identical
    to the map. Samples and aggregate profiles are not exposed to mapped roles.
    """
    result = resolve(server, actor, value)
    node = result['node']
    detail = {'kind': node['kind']}
    role = server._session_pg_role(actor)
    with server._conn(read_only=True) as conn:
        conn.execute("SET LOCAL statement_timeout='8s'")
        if node['kind'] in ('relation', 'cube'):
            rows = conn.execute(
                "SELECT col_name,data_type,role,what,basis,updated_at FROM rvbbit.meaning_columns "
                "WHERE relation=%s ORDER BY col_name LIMIT 81", (node['source'],)).fetchall()
            detail.update(fields=rows[:80], fields_more=len(rows) > 80)
            if node['kind'] == 'cube':
                rows = conn.execute(
                    "SELECT c.column_name AS col_name,c.data_type,'cube field' AS role,"
                    "concat_ws(E'\\n',nullif(d.doc,''),nullif(d.semantics,'')) AS what,"
                    "CASE WHEN d.edited_by IS NOT NULL THEN 'edited cube documentation' "
                    "WHEN d.doc IS NOT NULL THEN 'generated cube documentation' ELSE 'column schema' END AS basis,"
                    "d.updated_at,d.source_ref FROM information_schema.columns c "
                    "LEFT JOIN rvbbit.cube_columns d ON d.cube_name=c.table_name AND d.column_name=c.column_name "
                    "WHERE c.table_schema='cubes' AND c.table_name=%s ORDER BY c.ordinal_position LIMIT 81",
                    (node['cube'],)).fetchall()
                detail.update(fields=rows[:80], fields_more=len(rows) > 80)
                definition = conn.execute('SELECT sql,grain,description,labels,version FROM rvbbit.cube_defs WHERE name=%s AND version=%s',
                                          (node['cube'], node['version'])).fetchone()
                detail.update(definition or {})
        elif node['kind'] == 'metric':
            definition = conn.execute(
                "SELECT sql,params,grain,labels FROM rvbbit.metric_defs WHERE name=%s AND version=%s",
                (node['metric'], node['version'])).fetchone()
            if not definition:
                raise ActionError('NOT_FOUND', 'This metric definition is no longer available', 404)
            labels = definition.get('labels') or {}
            detail.update(sql=definition['sql'], defaults=definition['params'],
                          grain=definition.get('grain'), window=labels.get('window'),
                          why=labels.get('why'), grain_columns=array(labels.get('grain_columns')),
                          observations=[], observation_scope=definition['params'] or {},
                          contract=labels.get('metric_contract'), review=node.get('review', []),
                          snapshot_basis='Last saved observation per UTC day, current definition and default scope',
                          observations_restricted=bool(role))
            if not role:
                rows = conn.execute(
                    "SELECT DISTINCT ON ((observed_at AT TIME ZONE 'UTC')::date) value,observed_at,data_as_of,extract(epoch FROM observed_at) AS time "
                    "FROM rvbbit.metric_default_observations WHERE metric_name=%s AND metric_version=%s "
                    "ORDER BY (observed_at AT TIME ZONE 'UTC')::date DESC,observed_at DESC,observation_id DESC LIMIT 24", (node['metric'], node['version'])).fetchall()
                detail['observations'] = [dict(r, value=r['value'].get('value') if isinstance(r['value'], dict) else r['value']) for r in rows]
    result['detail'] = detail
    return result


def prepare(server):
    with _lock:
        if server._conn not in _ready:
            with server._conn() as conn:
                conn.execute(DDL)
            _ready.add(server._conn)


def pins(server, actor):
    prepare(server)
    with server._conn() as conn:
        rows = conn.execute("SELECT id,reference FROM rvbbit.workspace_object_pins WHERE actor=%s AND reference->>'kind'='meaning' ORDER BY created_at,id LIMIT 32", (actor,)).fetchall()
    return {'pins': [{'id': str(r['id']), 'reference': r['reference']} for r in rows]}


def change_pin(server, actor, body):
    prepare(server)
    if not isinstance(body, dict):
        raise ValueError('Expected a pin operation')
    if set(body) == {'operation', 'id'} and body['operation'] == 'remove':
        ident = str(uuid.UUID(str(body['id'])))
        with server._conn() as conn:
            conn.execute('DELETE FROM rvbbit.workspace_object_pins WHERE id=%s AND actor=%s', (ident, actor))
        return {'removed': ident}
    if set(body) != {'operation', 'reference'} or body['operation'] != 'pin':
        raise ValueError('Use pin with reference, or remove with id')
    value = reference(body['reference'])
    resolve(server, actor, value)
    with server._conn() as conn:
        with conn.transaction():
            conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', ('workspace-pins:' + actor,))
            old = conn.execute('SELECT id FROM rvbbit.workspace_object_pins WHERE actor=%s AND reference=%s::jsonb', (actor, json.dumps(value))).fetchone()
            if old:
                return {'id': str(old['id']), 'reference': value}
            count = conn.execute('SELECT count(*) AS n FROM rvbbit.workspace_object_pins WHERE actor=%s', (actor,)).fetchone()['n']
            if count >= 32:
                raise ActionError('PIN_LIMIT', 'Your workspace supports 32 Meaning pins', 409)
            ident = str(uuid.uuid4())
            conn.execute('INSERT INTO rvbbit.workspace_object_pins(id,actor,reference) VALUES(%s,%s,%s::jsonb)', (ident, actor, json.dumps(value)))
    return {'id': ident, 'reference': value}


def register(m, server, session_actor, respond):
    async def dispatch(request, fn, post=False):
        if post:
            allowed = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
            if request.headers.get('sec-fetch-site') == 'cross-site' or (request.headers.get('origin') and request.headers['origin'] not in allowed) or request.headers.get('content-type', '').split(';')[0] != 'application/json':
                return respond({'error': {'code': 'INVALID_ORIGIN'}}, 403)
        raw = await request.body() if post else b'{}'
        if len(raw) > 8000:
            return respond({'error': {'code': 'TOO_LARGE'}}, 413)
        def run():
            actor, _, error = session_actor(request)
            if error:
                return error
            token = server._SESSION_SUB.set(actor)
            try:
                return respond(fn(actor, json.loads(raw)))
            except ActionError as e:
                return respond({'error': {'code': e.code, 'message': str(e)}}, e.status)
            except (ValueError, TypeError, KeyError) as e:
                return respond({'error': {'code': 'INVALID_MEANING_REQUEST', 'message': str(e)}}, 400)
            except Exception:
                LOG.exception('Meaning graph unavailable')
                return respond({'error': {'code': 'MEANING_UNAVAILABLE', 'message': 'Meaning could not refresh. Please retry.'}}, 503)
            finally:
                server._SESSION_SUB.reset(token)
        return await asyncio.get_running_loop().run_in_executor(server._CONTROL_EXECUTOR, contextvars.copy_context().run, run)

    @m.custom_route('/api/calliope/meaning/graph', methods=['GET'])
    async def get_graph(request):
        return await dispatch(request, lambda actor, _: graph(server, actor, request.query_params.get('area', ''), int(request.query_params.get('limit', 12)), request.query_params.get('focus', '')))

    @m.custom_route('/api/calliope/meaning/object', methods=['POST'])
    async def get_object(request):
        return await dispatch(request, lambda actor, body: resolve(server, actor, body), True)

    @m.custom_route('/api/calliope/meaning/inspect', methods=['POST'])
    async def get_inspection(request):
        return await dispatch(request, lambda actor, body: inspect_object(server, actor, body), True)

    @m.custom_route('/api/calliope/meaning/pins', methods=['GET'])
    async def get_pins(request):
        return await dispatch(request, lambda actor, _: pins(server, actor))

    @m.custom_route('/api/calliope/meaning/pins', methods=['POST'])
    async def post_pin(request):
        return await dispatch(request, lambda actor, body: change_pin(server, actor, body), True)

    import meaning_web
    meaning_web.register(m, server, session_actor, dispatch)
