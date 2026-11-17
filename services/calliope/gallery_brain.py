"""Admin knowledge inventory and bounded structural graphs. No inference or body reads.

Uses the same brain KG and Hindsight link tables as DataRabbit. Cache counts and
samples briefly; never scan the full graph for layout or manufacture semantic links.
"""
from collections import OrderedDict
from datetime import datetime, timezone
import logging
import threading
import time

from psycopg import Error
import calliope

log = logging.getLogger(__name__)
_lock = threading.Lock()
_cache = OrderedDict()
TTL = 60


def _read(conn, name, query, params, unavailable):
    try:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout='3s'")
            return conn.execute(query, params).fetchall()
    except Error as exc:
        log.info('Brain inventory %s unavailable (%s)', name, exc.sqlstate)
        unavailable.append(name)
        return []


def _cached(key, build):
    now = time.monotonic()
    found = _cache.get(key)
    if found and now-found[0] < TTL:
        _cache.move_to_end(key)
        return found[1]
    result = build()
    _cache[key] = (time.monotonic(), result)
    _cache.move_to_end(key)
    while len(_cache) > 32:
        _cache.popitem(last=False)
    return result


def _agent_identities():
    """Reuse Hermes profile identity metadata, with a short optional deadline."""
    import hermes_profiles
    try:
        payload = hermes_profiles._control('GET', '/v1/profiles', timeout=2)
        return {p['key']: p for p in payload.get('profiles', [])
                if isinstance(p, dict) and isinstance(p.get('key'), str)}
    except Exception:
        log.info('Brain agent portraits temporarily unavailable')
        return {}


def _attach_agents(items, profiles):
    for item in items:
        if item['family'] != 'memory':
            continue
        bank = item['ref'][5:]
        key = 'default' if bank == 'hermes' else bank.removeprefix('calliope-')
        identity = profiles.get(key)
        if not identity and bank != 'hermes':
            continue
        identity = identity or {'name': 'Calliope', 'avatar_night_file': 'callie-avatar-night.jpg'}
        item['agent_name'] = identity.get('name') or key
        item['agent_profile'] = key
        item['avatar_url'] = calliope._agent_avatar_url(identity.get('avatar_file') or
            identity.get('avatar_night_file') or identity.get('avatar_day_file'))


def _inventory(conn, profiles=None):
    missing = []
    sources = _read(conn, 'Document sources', '''SELECT source_id,label,kind,enabled,
      created_at,last_synced_at FROM rvbbit.brain_sources ORDER BY lower(label),source_id''', (), missing)
    banks = _read(conn, 'Hindsight banks', '''SELECT bank_id,name,created_at,updated_at
      FROM hindsight.banks ORDER BY lower(coalesce(name,bank_id)),bank_id''', (), missing)
    items = [dict(ref='source:'+str(s['source_id']), family='documents', name=s['label'],
                  kind=s['kind'], enabled=s['enabled'], updated_at=s['last_synced_at'],
                  created_at=s['created_at'], documents=None, chunks=None, ready=None, pending=None, errors=None)
             for s in sources]
    items += [dict(ref='bank:'+b['bank_id'], family='memory', name=b['name'] or b['bank_id'],
                   kind='Hindsight', updated_at=b['updated_at'], created_at=b['created_at'],
                   documents=None, memories=None, links=None, entities=None)
              for b in banks]
    _attach_agents(items, profiles or {})
    lookup = {s['ref']: s for s in items}
    if sources:
        stats = _read(conn, 'Document counts', '''SELECT source_id,count(*) AS documents,
          count(*) FILTER(WHERE enriched_at IS NOT NULL AND enrich_hash IS NOT DISTINCT FROM content_hash
            AND enriched_at>=ingested_at) AS ready,
          count(*) FILTER(WHERE enrich_last_error IS NOT NULL) AS errors,
          max(enriched_at) AS enriched_at,max(ingested_at) AS ingested_at
          FROM rvbbit.brain_documents WHERE deleted_at IS NULL GROUP BY source_id''', (), missing)
        if 'Document counts' not in missing:
            for s in items:
                if s['family']=='documents':
                    s.update(documents=0, ready=0, pending=0, errors=0)
            for row in stats:
                s = lookup.get('source:'+str(row['source_id']))
                if s:
                    s.update({k:v for k,v in row.items() if k!='source_id'})
                    s['pending'] = s['documents']-s['ready']
        chunks = _read(conn, 'Document chunks', '''SELECT d.source_id,count(*) AS chunks
          FROM rvbbit.brain_chunks c JOIN rvbbit.brain_documents d USING(doc_id)
          WHERE d.deleted_at IS NULL GROUP BY d.source_id''', (), missing)
        if 'Document chunks' not in missing:
            for s in items:
                if s['family']=='documents': s['chunks']=0
            for row in chunks:
                if s:=lookup.get('source:'+str(row['source_id'])): s['chunks']=row['chunks']
    if banks:
        # Separate queries keep a slow optional count from erasing the bank list.
        for table, field in [('documents','documents'),('memory_units','memories'),
                             ('memory_links','links'),('entities','entities')]:
            label='Hindsight '+field
            extra=',max(created_at) AS latest_memory_at' if table=='memory_units' else ''
            rows = _read(conn, label, f'SELECT bank_id,count(*) AS n{extra} FROM hindsight.{table} GROUP BY bank_id', (), missing)
            if label not in missing:
                for s in items:
                    if s['family']=='memory': s[field]=0
                for row in rows:
                    if s:=lookup.get('bank:'+row['bank_id']):
                        s[field]=row['n']
                        if 'latest_memory_at' in row: s['latest_memory_at']=row['latest_memory_at']
    knowledge = _read(conn, 'Knowledge graphs', """SELECT graph_id,kind,count(*) AS nodes,
      max(updated_at) AS updated_at FROM rvbbit.kg_nodes
      WHERE graph_id IN ('db_catalog','business_context') GROUP BY graph_id,kind""", (), missing)
    for graph, family, name in [('db_catalog','catalog','Database catalog'),
                                 ('business_context','context','Business topology')]:
        kinds = [r for r in knowledge if r['graph_id']==graph]
        if not kinds:
            continue
        dates = [r['updated_at'] for r in kinds if r['updated_at']]
        items.append(dict(ref='graph:'+graph, family=family, name=name, kind=name,
                          nodes=sum(r['nodes'] for r in kinds),
                          counts={r['kind']:r['nodes'] for r in kinds},
                          updated_at=max(dates) if dates else None))
    return dict(items=items, unavailable=missing, observed_at=datetime.now(timezone.utc).isoformat())


def _graph(conn, inventory, ref):
    unavailable=[]
    selected=[i for i in inventory['items'] if not ref or i['ref']==ref]
    # All configured sources remain in inventory; graph cost has an explicit cap.
    roots=selected[:80]
    nodes={i['ref']:dict(id=i['ref'], label=i['name'], kind=i['family'], root=True, sources=[i['ref']]) for i in roots}
    edges={}
    def node(key, label, kind, source):
        if key not in nodes: nodes[key]=dict(id=key,label=label,kind=kind,root=False,sources=[])
        if source not in nodes[key]['sources']: nodes[key]['sources'].append(source)
    def edge(a,b,kind):
        if a in nodes and b in nodes and a!=b:
            edges[(a,b,kind)]=dict(source=a,target=b,kind=kind)
    per_root=100 if ref else max(4,min(12,240//max(len(roots),1)))
    source_ids=[int(s['ref'][7:]) for s in roots if s['family']=='documents']
    if source_ids:
        docs=_read(conn,'Document graph', '''SELECT d.doc_id,d.source_id,n.node_id FROM
          unnest(%s::bigint[]) s(source_id) CROSS JOIN LATERAL (
            SELECT doc_id,source_id FROM rvbbit.brain_documents WHERE source_id=s.source_id
            AND deleted_at IS NULL ORDER BY doc_id DESC LIMIT %s) d
          LEFT JOIN rvbbit.kg_nodes n ON n.graph_id='brain' AND n.kind='document'
            AND n.label_norm=lower(rvbbit.brain_doc_label(d.doc_id))''', (source_ids,per_root), unavailable)
        owners={}
        for d in docs:
            root='source:'+str(d['source_id']); key='doc:'+str(d['doc_id'])
            node(key,'Document','document',root);edge(root,key,'contains')
            if d['node_id'] is not None: owners[d['node_id']]=(key,root)
        if owners:
            relations=_read(conn,'Document connections', '''SELECT ids.id AS document_node,e.object_node_id,
              e.predicate_norm,n.kind FROM unnest(%s::bigint[]) ids(id)
              CROSS JOIN LATERAL (SELECT object_node_id,predicate_norm FROM rvbbit.kg_edges
                WHERE graph_id='brain' AND subject_node_id=ids.id AND predicate_norm='mentions'
                ORDER BY object_node_id LIMIT 8) e
              JOIN rvbbit.kg_nodes n ON n.node_id=e.object_node_id AND n.graph_id='brain'
              WHERE n.kind<>'document' ''', (list(owners),), unavailable)
            for e in relations:
                key,root=owners[e['document_node']]; entity='entity:'+str(e['object_node_id'])
                node(entity,e['kind'],'entity',root);edge(key,entity,e['predicate_norm'])
    bank_ids=[s['ref'][5:] for s in roots if s['family']=='memory']
    if bank_ids:
        memories=_read(conn,'Memory graph', '''SELECT m.id::text,m.bank_id,m.fact_type FROM
          unnest(%s::text[]) b(bank_id) CROSS JOIN LATERAL (
            SELECT id,bank_id,fact_type FROM hindsight.memory_units WHERE bank_id=b.bank_id
            ORDER BY event_date DESC NULLS LAST,id LIMIT %s) m''', (bank_ids,per_root), unavailable)
        owners={}
        for m in memories:
            root='bank:'+m['bank_id'];key='memory:'+m['id'];owners[m['id']]=root
            node(key,m['fact_type'] or 'Memory','memory',root);edge(root,key,'contains')
        if owners:
            links=_read(conn,'Memory connections', '''SELECT a.id::text AS from_id,l.to_unit_id::text AS to_id,
              l.link_type,m.bank_id,m.fact_type FROM unnest(%s::uuid[]) a(id)
              CROSS JOIN LATERAL (SELECT to_unit_id,link_type,bank_id FROM hindsight.memory_links
                WHERE from_unit_id=a.id ORDER BY link_type,weight DESC,to_unit_id LIMIT 3) l
              JOIN hindsight.memory_units m ON m.id=l.to_unit_id AND m.bank_id=l.bank_id
              WHERE m.bank_id=ANY(%s::text[])''', (list(owners),bank_ids), unavailable)
            for l in links:
                root='bank:'+l['bank_id'];key='memory:'+l['to_id']
                node(key,l['fact_type'] or 'Memory','memory',root)
                edge(root,key,'contains');edge('memory:'+l['from_id'],key,l['link_type'])
            mentions=_read(conn,'Memory entities', '''SELECT a.id::text AS unit_id,e.id::text AS entity_id
              FROM unnest(%s::uuid[]) a(id) CROSS JOIN LATERAL (
                SELECT entity_id FROM hindsight.unit_entities WHERE unit_id=a.id ORDER BY entity_id LIMIT 3) u
              JOIN hindsight.entities e ON e.id=u.entity_id WHERE e.bank_id=ANY(%s::text[])''',
              (list(owners),bank_ids), unavailable)
            for e in mentions:
                root=owners[e['unit_id']];key='hentity:'+e['entity_id']
                node(key,'Entity','entity',root);edge('memory:'+e['unit_id'],key,'mentions')
    _knowledge_graph(conn, roots, nodes, edges, ref, unavailable)
    return dict(nodes=list(nodes.values()),edges=list(edges.values()),unavailable=unavailable,
                sample_per_source=per_root,shown_sources=len(roots),total_sources=len(selected))


def _knowledge_graph(conn, roots, nodes, edges, ref, unavailable):
    """Bounded KG projection, plus exact relation identity bridges. Never name-match entities."""
    graphs = [r['ref'][6:] for r in roots if r['family'] in ('catalog','context')]
    if not graphs:
        return
    limit = 120 if ref else 48
    # Round-robin across schemas/domains so one large schema cannot consume the sample.
    samples = _read(conn, 'Knowledge graph sample', """WITH ranked AS (
      SELECT node_id,graph_id,kind,label,properties->>'name' AS name,
        properties->>'relation' AS relation,
        row_number() OVER (PARTITION BY graph_id,
          CASE WHEN graph_id='db_catalog' THEN coalesce(properties->>'schema','')
               ELSE coalesce(properties->>'domain_key',kind) END
          ORDER BY CASE kind WHEN 'business_domain' THEN 0 WHEN 'business_value_system' THEN 1
            WHEN 'business_source_relation' THEN 2 ELSE 3 END,node_id) AS rank
      FROM rvbbit.kg_nodes WHERE graph_id=ANY(%s::text[]) AND
        (graph_id='business_context' OR kind IN ('db_schema','db_table','cube'))
    ) SELECT s.node_id,s.graph_id,s.kind,s.label,
        s.name,s.relation
      FROM unnest(%s::text[]) g(graph_id) CROSS JOIN LATERAL
        (SELECT * FROM ranked WHERE graph_id=g.graph_id ORDER BY rank,node_id LIMIT %s) s""",
      (graphs,graphs,limit), unavailable)
    def add(row):
        key='kg:'+str(row['node_id']);owner='graph:'+row['graph_id']
        nodes[key]=dict(id=key,label=row.get('name') or row['label'],kind=row['kind'],
                        family='catalog' if row['graph_id']=='db_catalog' else 'context',
                        root=False,sources=[owner])
        edges[(owner,key,'contains')]=dict(source=owner,target=key,kind='contains')
    for row in samples:
        add(row)
    if not samples:
        return
    # Both directions: leaf samples retain their recorded parents and vice versa.
    neighbors = _read(conn, 'Knowledge graph connections', """SELECT e.*,
        n.graph_id,n.kind,n.label,n.properties->>'name' AS name,
        n.properties->>'relation' AS relation FROM unnest(%s::bigint[]) seeds(id)
      CROSS JOIN LATERAL (
        SELECT * FROM (
          (SELECT subject_node_id,object_node_id,predicate_norm,object_node_id AS node_id
           FROM rvbbit.kg_edges WHERE subject_node_id=seeds.id AND graph_id=ANY(%s::text[])
           ORDER BY edge_id LIMIT 3)
          UNION ALL
          (SELECT subject_node_id,object_node_id,predicate_norm,subject_node_id AS node_id
           FROM rvbbit.kg_edges WHERE object_node_id=seeds.id AND graph_id=ANY(%s::text[])
           ORDER BY edge_id LIMIT 3)
        ) adjacent
      ) e JOIN rvbbit.kg_nodes n ON n.node_id=e.node_id
      WHERE n.graph_id=ANY(%s::text[]) AND n.kind<>'db_column'""",
      ([r['node_id'] for r in samples],graphs,graphs,graphs), unavailable)
    for row in neighbors:
        add(row)
    for row in neighbors:
        a='kg:'+str(row['subject_node_id']);b='kg:'+str(row['object_node_id']);kind=row['predicate_norm']
        if a in nodes and b in nodes:
            edges[(a,b,kind)]=dict(source=a,target=b,kind=kind)
    if set(graphs)=={'db_catalog','business_context'}:
        source_ids=list({r['node_id'] for r in samples+neighbors
                         if r['graph_id']=='business_context' and r.get('relation')})
        if source_ids:
            bridges=_read(conn, 'Catalog identity bridges', """SELECT b.node_id AS context_id,
              c.node_id,c.graph_id,c.kind,c.label FROM unnest(%s::bigint[]) ids(id)
              JOIN rvbbit.kg_nodes b ON b.node_id=ids.id
              CROSS JOIN LATERAL (SELECT node_id,graph_id,kind,label FROM rvbbit.kg_nodes
                WHERE graph_id='db_catalog' AND kind='db_table'
                  AND label_norm=lower(b.properties->>'relation')
                ORDER BY node_id LIMIT 1) c""", (source_ids,), unavailable)
            for row in bridges:
                add(row)
                a='kg:'+str(row['context_id']);b='kg:'+str(row['node_id']);kind='same_table'
                edges[(a,b,kind)]=dict(source=a,target=b,kind=kind)


def snapshot(factory, owner, ref=''):
    if not calliope.calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError('Brain inventory requires a Calliope administrator')
    if not isinstance(ref,str) or len(ref)>300:
        raise ValueError('Invalid knowledge source')
    # Serialize cache misses so multiple tabs do not duplicate graph/count scans.
    with _lock:
        profiles = _cached((factory,'profiles'), _agent_identities)
        with factory() as conn, conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            conn.execute("SET LOCAL lock_timeout='400ms'")
            inventory=_cached((factory,'inventory'),lambda:_inventory(conn,profiles))
            if ref and ref not in {i['ref'] for i in inventory['items']}:
                raise LookupError('Knowledge source is no longer available')
            graph=_cached((factory,'graph',ref),lambda:_graph(conn,inventory,ref))
    return dict(**inventory,graph=graph,selected=ref,cache_seconds=TTL)
