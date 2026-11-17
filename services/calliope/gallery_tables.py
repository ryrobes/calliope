"""Read-only Gallery inventory over live metadata and already stored observations.

No crawl, count, analysis, or inference is triggered here. Sampling is a separate
short, read-only request; optional catalogs fail independently of core metadata.
"""
import json
import logging
import re
from psycopg import sql, Error
import calliope

log = logging.getLogger(__name__)
KINDS = {'r': 'Table', 'p': 'Partitioned table', 'm': 'Materialized view', 'v': 'View', 'f': 'Foreign table'}


def require_admin(factory, owner):
    if not calliope.calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError('Tables inventory requires a Calliope administrator')


def allowed(schema):
    from server import _schema_allowed
    return (_schema_allowed(schema) and schema != 'hindsight' and not schema.startswith('_')
            and not schema.endswith('_staging'))


def _oid(ref):
    if not isinstance(ref, str) or not re.fullmatch(r'table:[1-9][0-9]{0,9}', ref):
        raise ValueError('Invalid table reference')
    oid = int(ref[6:])
    if oid > 4294967295:
        raise ValueError('Invalid table reference')
    return oid


def _optional(conn, name, query, params, unavailable):
    try:
        with conn.transaction():
            conn.execute("SET LOCAL statement_timeout='2s'")
            return conn.execute(query, params).fetchall()
    except Error as exc:
        # Raw database errors can include row contents; never send them to the UI.
        log.info('Gallery table source %s unavailable (%s)', name, exc.sqlstate)
        unavailable.append(name)
        return []



def _read_setup(conn, owner):
    from server import _session_pg_role
    conn.execute('SET TRANSACTION READ ONLY')
    role = _session_pg_role(owner)
    if role:
        conn.execute(sql.SQL('SET LOCAL ROLE {}').format(sql.Identifier(role)))
    conn.execute("SET LOCAL statement_timeout='5s'")
    conn.execute("SET LOCAL lock_timeout='500ms'")


def _base(conn, oid=None):
    rows = conn.execute('''SELECT c.oid::bigint AS oid,n.nspname AS schema,c.relname AS name,c.relkind,
      format('%%I.%%I',n.nspname,c.relname) AS qualified_name,
      left(obj_description(c.oid,'pg_class'),2000) AS description,
      CASE WHEN c.reltuples<0 THEN NULL ELSE c.reltuples::bigint END AS estimated_rows,
      (SELECT count(*) FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped) AS column_count,
      s.last_analyze,s.last_autoanalyze,greatest(s.last_analyze,s.last_autoanalyze) AS analyzed_at,
      c.relispartition AS is_partition,c.relispopulated AS populated
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid
      WHERE c.relkind IN ('r','p','m','v','f') AND (%s::oid IS NULL OR c.oid=%s::oid)
        AND n.nspname NOT IN ('rvbbit','hindsight','information_schema') AND n.nspname !~ '^pg_'
        AND c.relname NOT LIKE '\\_dlt\\_%%'
        AND has_schema_privilege(n.oid,'USAGE') AND has_table_privilege(c.oid,'SELECT')
      ORDER BY n.nspname,c.relname''', (oid, oid)).fetchall()
    items = [dict(r, ref='table:'+str(r['oid']), kind=KINDS[r['relkind']], history=[])
             for r in rows if allowed(r['schema'])]
    if oid and not items:
        raise LookupError('Table is no longer available in this inventory')
    return items


def _history(conn, items, unavailable):
    if not items:
        return
    rows = _optional(conn, 'Catalog history', '''SELECT t.oid,h.* FROM
      unnest(%s::bigint[],%s::text[]) t(oid,obj_key)
      CROSS JOIN LATERAL (SELECT s.run_id,s.captured_at,s.fingerprint
        FROM rvbbit.catalog_snapshots s JOIN rvbbit.catalog_runs r USING(run_id)
        WHERE s.graph_id='db_catalog' AND r.graph_id=s.graph_id AND r.status='ok'
          AND s.obj_key=t.obj_key AND s.kind='db_table'
          AND s.fingerprint->>'oid'=t.oid::text
        ORDER BY s.run_id DESC LIMIT 24) h''',
        ([i['oid'] for i in items], [i['schema']+'.'+i['name'] for i in items]), unavailable)
    lookup = {i['oid']: i for i in items}
    for row in rows:
        fp = row['fingerprint']
        lookup[row['oid']]['history'].append(dict(run_id=row['run_id'], captured_at=row['captured_at'],
            rows=fp.get('n_rows'), sampled=fp.get('sampled'), sample_rows=fp.get('n_sampled'),
            size_bytes=fp.get('size_bytes'), column_count=fp.get('n_columns')))
    for item in items:
        item['history'].sort(key=lambda h: h['run_id'], reverse=True)
        item['observed_at'] = item['history'][0]['captured_at'] if item['history'] else None
        item['rows'] = item['history'][0]['rows'] if item['history'] else item['estimated_rows']
        item['row_source'] = 'Catalog observation' if item['history'] else 'Planner estimate'


def _business_assessments(conn, items, unavailable):
    """Reuse relation-specific interpretations from current semantic families.

    Expand the saved graphs once per request, rather than once per card. Only
    attach memberships to live, authorized inventory objects with exact names.
    """
    lookup = {item['qualified_name']: item for item in items}
    for item in items:
        item['business_assessments'] = []
    if not items:
        return
    rows = _optional(conn, 'Business assessments', '''
      SELECT g.graph_id::text,g.name,g.status,g.lifecycle,g.updated_at,
        m->>'relation' AS relation,m->>'role' AS role,
        left(m->>'rationale',3000) AS rationale,m->>'confidence' AS confidence
      FROM rvbbit.business_value_graph_current g
      CROSS JOIN LATERAL jsonb_array_elements(CASE
        WHEN jsonb_typeof(g.graph->'source_memberships')='array'
        THEN g.graph->'source_memberships' ELSE '[]'::jsonb END) m
      WHERE m->>'relation'=ANY(%s::text[]) AND m->>'relation'=ANY(g.source_relations)
        AND nullif(btrim(m->>'rationale'),'') IS NOT NULL
      ORDER BY (g.lifecycle='canonical') DESC,g.usable DESC,g.updated_at DESC,g.graph_id,
        m->>'relation',m->>'role',m->>'rationale'
      ''', (list(lookup),), unavailable)
    for row in rows:
        item = lookup[row['relation']]
        if len(item['business_assessments']) >= 8:
            continue
        try:
            confidence = float(row['confidence'])
            if not 0 <= confidence <= 1:
                confidence = None
        except (TypeError, ValueError):
            confidence = None
        item['business_assessments'].append(dict(graph_id=row['graph_id'], name=row['name'],
            status=row['status'], lifecycle=row['lifecycle'], updated_at=row['updated_at'],
            role=row['role'], rationale=row['rationale'], confidence=confidence))


def index(factory, owner, query='', schema='', offset=0):
    require_admin(factory, owner)
    unavailable = []
    try:
        offset = max(0, int(offset))
    except (ValueError, TypeError):
        raise ValueError('Invalid table offset')
    query = str(query or '')[:200].lower().strip()
    with factory() as conn, conn.transaction():
        _read_setup(conn, owner)
        all_items = _base(conn)
        _business_assessments(conn, all_items, unavailable)
        schemas = sorted({i['schema'] for i in all_items})
        matched = [i for i in all_items if (not schema or i['schema']==schema) and
                   (not query or query in ' '.join([i['qualified_name'], i['description'] or '', i['kind'],
                    *[str(a.get(k) or '') for a in i['business_assessments'] for k in ('name','role','rationale')]]).lower())]
        items = matched[offset:offset+60]
        _history(conn, items, unavailable)
    return dict(tables=items, total=len(matched), inventory_total=len(all_items), schemas=schemas,
                next_offset=offset+60 if offset+60<len(matched) else None, unavailable=unavailable)


def _short(value, limit=256):
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    return text[:limit] + ('…' if len(text)>limit else '')


def _profiles(conn, table, unavailable):
    history = table['history']
    if not history:
        return [], [], False
    rows = _optional(conn, 'Saved column profiles', '''SELECT s.run_id,s.col_name,s.fingerprint
      FROM rvbbit.catalog_snapshots s WHERE s.graph_id='db_catalog'
        AND s.run_id=ANY(%s::bigint[]) AND s.kind='db_column'
        AND s.schema_name=%s AND s.rel_name=%s ORDER BY s.run_id DESC,s.col_name LIMIT 4000''',
        ([h['run_id'] for h in history[:2]], table['schema'], table['name']), unavailable)
    by_run = {h['run_id']: {} for h in history[:2]}
    for r in rows:
        by_run[r['run_id']][r['col_name']] = r['fingerprint']
    current = by_run[history[0]['run_id']]
    profiles = [dict(name=name, null_frac=fp.get('null_frac'), distinct=fp.get('ndv'),
        method=fp.get('ndv_method'), seen=fp.get('n_seen'), min=_short(fp.get('min')),
        max=_short(fp.get('max')), examples=[_short(v) for v in (fp.get('example_values') or [])[:5]],
        quantiles=fp.get('quantiles')) for name, fp in current.items()]
    changes = []
    if len(history)<2 or 'Saved column profiles' in unavailable:
        return profiles, changes, False
    prior = by_run[history[1]['run_id']]
    for name in sorted(set(current)|set(prior)):
        a,b = prior.get(name),current.get(name)
        if a is None or b is None:
            changes.append(dict(column=name, field='Column', before='Present' if a else 'Absent', after='Present' if b else 'Absent'))
            continue
        for key,label in [('data_type','Type'),('nullable','Nullable'),('is_pk','Primary key'),('fk_target','References')]:
            if a.get(key)!=b.get(key):
                changes.append(dict(column=name,field=label,before=_short(a.get(key)),after=_short(b.get(key))))
        # Profile changes are observations, not declarations of semantic breakage.
        for key,label in [('null_frac','Null fraction'),('ndv','Distinct values'),('min','Minimum'),('max','Maximum')]:
            if a.get(key)!=b.get(key):
                changes.append(dict(column=name,field=label,before=_short(a.get(key)),after=_short(b.get(key))))
    complete = all(len(by_run[h['run_id']])==h.get('column_count') for h in history[:2])
    return profiles, changes, complete


def detail(factory, owner, ref):
    require_admin(factory, owner)
    oid = _oid(ref)
    unavailable = []
    with factory() as conn, conn.transaction():
        _read_setup(conn, owner)
        table = _base(conn, oid)[0]
        _business_assessments(conn, [table], unavailable)
        _history(conn, [table], unavailable)
        table['columns'] = conn.execute('''SELECT a.attname AS name,format_type(a.atttypid,a.atttypmod) AS type,
          NOT a.attnotnull AS nullable,left(col_description(a.attrelid,a.attnum),1000) AS description,
          EXISTS(SELECT 1 FROM pg_constraint k WHERE k.conrelid=a.attrelid AND k.contype='p' AND a.attnum=ANY(k.conkey)) AS primary_key,
          st.null_frac AS planner_null_frac,st.n_distinct AS planner_distinct
          FROM pg_attribute a LEFT JOIN pg_stats st ON st.schemaname=%s AND st.tablename=%s AND st.attname=a.attname
          WHERE a.attrelid=%s::oid AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum''',
          (table['schema'],table['name'],oid)).fetchall()
        table['relationships'] = conn.execute('''SELECT k.conname AS name,
          k.conrelid::regclass::text AS source,k.confrelid::regclass::text AS target,
          pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
          WHERE k.contype='f' AND (k.conrelid=%s::oid OR k.confrelid=%s::oid)
          ORDER BY k.conname LIMIT 100''',(oid,oid)).fetchall()
        size = _optional(conn,'Storage',"SELECT pg_total_relation_size(%s::oid) AS bytes",(oid,),unavailable)
        table['size_bytes'] = size[0]['bytes'] if size else None
        table['profiles'], changes, comparable = _profiles(conn, table, unavailable)
        table['drift'] = dict(comparable=comparable,
                              changes=changes[:100], total=len(changes))
        docs = _optional(conn,'Catalog description', '''SELECT left(doc,4000) AS doc,updated_at FROM rvbbit.catalog_docs
          WHERE graph_id='db_catalog' AND kind='db_table' AND schema_name=%s AND rel_name=%s
          ORDER BY updated_at DESC LIMIT 1''',(table['schema'],table['name']),unavailable)
        table['catalog_description'] = docs[0] if docs and table['history'] else None
        accel = _optional(conn,'Acceleration', '''SELECT parquet_rows,row_groups,parquet_bytes,last_refresh_at,
          drift_rows,shadow_heap_dirty FROM rvbbit.accel_freshness WHERE table_oid=%s::oid LIMIT 1''',(oid,),unavailable)
        table['acceleration'] = accel[0] if accel else None
        # The current graph contains authored / inferred context. Do not expose
        # executable metric SQL or run a new semantic investigation on opening.
        contexts = _optional(conn,'Business context', '''SELECT graph_id::text,name,left(summary,3000) AS summary,
          status,updated_at,graph->'source_memberships' AS memberships,graph->'open_questions' AS questions
          FROM rvbbit.business_value_graph_current WHERE to_jsonb(source_relations) @> %s::jsonb
          ORDER BY updated_at DESC LIMIT 12''',(json.dumps([table['qualified_name']]),),unavailable)
        table['business_context'] = [dict(graph_id=c['graph_id'],name=c['name'],summary=c['summary'],status=c['status'],updated_at=c['updated_at'],
            memberships=[{k:m.get(k) for k in ['role','rationale','confidence']} for m in (c['memberships'] or []) if m.get('relation')==table['qualified_name']],
            questions=[_short(q,1000) for q in (c['questions'] or [])[:5]]) for c in contexts]
        from gallery_jobs import relations
        managed = _optional(conn, 'SQL jobs', "SELECT id::text,name,sql,enabled,interval_seconds,next_run_at FROM rvbbit.calliope_workspace_jobs ORDER BY name LIMIT 501", (), unavailable)
        table['jobs'] = []
        for job in managed[:500]:
            lineage = relations(job['sql'])
            names = {table['qualified_name'], table['schema']+'.'+table['name']}
            direction = 'Maintains' if names.intersection(lineage['destinations']) else 'Reads' if names.intersection(lineage['sources']) else None
            if direction:
                table['jobs'].append(dict(ref='sql:'+job['id'],name=job['name'],kind='SQL ETL',relationship=direction,
                    enabled=job['enabled'],interval_seconds=job['interval_seconds'],next_run_at=job['next_run_at']))
        if len(managed)>500:
            unavailable.append('Additional SQL job relationships (first 500 checked)')
        mirrors = _optional(conn,'Mirror jobs', """SELECT j.job_name,j.enabled,j.schedule_seconds AS interval_seconds,j.next_run_at
          FROM rvbbit.mirror_jobs j JOIN rvbbit.mirror_tables t USING(job_name)
          WHERE j.destination_schema=%s AND t.destination_table=%s ORDER BY j.job_name LIMIT 50""",(table['schema'],table['name']),unavailable)
        table['jobs'].extend(dict(ref='mirror:'+j['job_name'],name=j['job_name'],kind='Mirror',relationship='Maintains',
            enabled=j['enabled'],interval_seconds=j['interval_seconds'],next_run_at=j['next_run_at']) for j in mirrors)
    table['unavailable'] = unavailable
    return table


def sample(factory, owner, ref):
    require_admin(factory, owner)
    oid = _oid(ref)
    try:
        with factory() as conn, conn.transaction():
            _read_setup(conn, owner)
            conn.execute("SET LOCAL statement_timeout='1500ms'")
            conn.execute("SET LOCAL lock_timeout='300ms'")
            table = _base(conn,oid)[0]
            if table['relkind'] not in ('r','p','m') or not table['populated']:
                return dict(columns=[],rows=[],note='Preview is available for stored tables and populated materialized views. Ask Calliope to query this object.')
            # Lock the named object, then recheck its OID to avoid sampling a
            # different table if a concurrent rename/replacement occurred.
            ident = sql.Identifier(table['schema'],table['name'])
            conn.execute(sql.SQL('SELECT 1 FROM {} LIMIT 0').format(ident))
            found = conn.execute('SELECT to_regclass(%s)::oid::bigint AS oid',(table['qualified_name'],)).fetchone()
            if found['oid']!=oid:
                raise LookupError('Table changed while opening; refresh the inventory')
            columns = conn.execute('''SELECT attname AS name,atttypid='bytea'::regtype AS binary
              FROM pg_attribute WHERE attrelid=%s::oid AND attnum>0 AND NOT attisdropped ORDER BY attnum LIMIT 20''',(oid,)).fetchall()
            if not columns:
                return dict(columns=[],rows=[],note='This table has no columns.')
            expressions = [sql.SQL("{} AS {}").format(sql.SQL("'[binary]'::text") if c['binary'] else
                sql.SQL('left({}::text,257)').format(sql.Identifier(c['name'])),sql.Identifier(c['name'])) for c in columns]
            rows = conn.execute(sql.SQL('SELECT {} FROM {} LIMIT 8').format(sql.SQL(',').join(expressions),ident)).fetchall()
            return dict(columns=[c['name'] for c in columns],rows=[{k:_short(v) for k,v in r.items()} for r in rows],
                note='Up to 8 rows · first 20 columns · cells capped at 256 characters. Unordered preview, not a representative sample.',
                observed_at=conn.execute('SELECT now() AS at').fetchone()['at'])
    except Error:
        return dict(columns=[],rows=[],note='Preview could not finish within its read limit or is unavailable. Metadata is still available; ask Calliope to investigate.')
