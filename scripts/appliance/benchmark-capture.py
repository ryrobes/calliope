#!/usr/bin/env python3
"""Internal collector, sent over SSH to the existing Warehouse container.

No inference and no writes to application state. PostgreSQL is captured in one
repeatable-read transaction; SQLite uses its own read snapshot. Boundaries and
coverage are explicit because these stores cannot share an atomic snapshot.
"""
import base64
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

SECRET = re.compile(r'(password|passwd|secret|credential|api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|private[_-]?key)', re.I)
STATE_TABLES = '''settings company_profile calliope_model_routes calliope_model_loadouts
provider_models provider_model_catalog model_rates model_price_snapshots model_alias_snapshots
calliope_response_preferences calliope_appearance_preferences calliope_workspace_settings
calliope_design_profiles calliope_design_profile_versions calliope_design_profile_assets
calliope_specialists generated_specialists generated_specialist_versions
calliope_dream_settings calliope_workflows calliope_session_schedules calliope_workspace_jobs
mirror_jobs hermes_cron_jobs meaning_relations meaning_columns meaning_links
meaning_recognitions meaning_company_map meaning_domains meaning_domain_versions
meaning_enrichments meaning_implied_metrics meaning_subject_aliases meaning_map_sources
meaning_overrides meaning_runs'''.split()
WINDOW_TABLES = '''calliope_agent_messages calliope_specialist_threads calliope_specialist_runs
calliope_specialist_messages calliope_specialist_findings calliope_work_order_runs
calliope_workflow_runs business_topology_inference_jobs meaning_model_calls'''.split()
ROOTS = (Path('/app/data/calliope'), Path('/app/data/captures'), Path('/opt/data/workspace'), Path('/opt/data/home'))
EXCLUDED = {'.git','node_modules','.venv','__pycache__','.ssh','.aws','.config','.cache','.local','.npm'}


def now(): return datetime.now(timezone.utc).isoformat()
def since(value,boundary):
    return datetime.fromisoformat(str(value).replace('Z','+00:00')) >= datetime.fromisoformat(str(boundary).replace('Z','+00:00'))
def encode(v):
    if isinstance(v, (bytes, memoryview)): return {'encoding':'base64','data':base64.b64encode(v).decode()}
    return str(v)
def digest(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()
def scrub(v):
    """Redact configuration credentials, never truncate transcripts or artifacts."""
    if isinstance(v,dict):
        if isinstance(v.get('key'),str) and SECRET.search(v['key']): return {**v,'value':'[redacted]'}
        return {k:('[redacted]' if SECRET.search(k) else scrub(x)) for k,x in v.items()}
    if isinstance(v,list): return [scrub(x) for x in v]
    if isinstance(v,str) and v.startswith(('http://','https://','postgresql://')):
        p=urlsplit(v)
        return urlunsplit((p.scheme,p.hostname or '',p.path,'','')) if p.username or p.query else v
    return v

def put(root,name,value):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,default=encode,ensure_ascii=False,indent=2)+'\n')
def lines(root,name,rows):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w') as f:
        for row in rows:f.write(json.dumps(row,default=encode,ensure_ascii=False)+'\n')
def safe_name(value):return re.sub(r'[^A-Za-z0-9_.-]','_',str(value))[:160]
def file_allowed(path):
    return not any(p in EXCLUDED or p.startswith('.env') or SECRET.search(p) or p in ('auth.json','config.yaml') for p in path.parts)

def context_index_snapshot(path, session_ids, originals=False):
    """Index coverage/version evidence; original text already lives in the native tape."""
    if not path.is_file() or not session_ids:return []
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    try:
        ids=sorted(session_ids)
        select=('SELECT session_id,tool_call_id,tool_name,content FROM originals' if originals else
                'SELECT id,session_id,message_id,role,tool_name,tool_call_id,chunk_offset,length(text) AS chars,'
                'vector IS NOT NULL AS embedded,model_version FROM chunks')
        return [dict(r) for r in db.execute(select+' WHERE session_id IN ('+','.join('?' for _ in ids)+')',ids)]
    finally:db.close()

def capture_skills(root, data_root=Path('/opt/data')):
    """Full before/after skill snapshots, not just changed scratch files."""
    inventory={}
    bases=[data_root/'skills', *sorted((data_root/'profiles').glob('*/skills'))]
    for base in bases:
        if not base.is_dir() or base.is_symlink():continue
        for path in sorted(base.rglob('*')):
            if not path.is_file() or path.is_symlink() or not file_allowed(path.relative_to(base)):continue
            if any(p.is_symlink() for p in path.parents):continue
            relative=path.relative_to(data_root)
            dest=root/'state/hermes-skills'/relative
            dest.parent.mkdir(parents=True,exist_ok=True)
            data=path.read_bytes();dest.write_bytes(data)
            inventory[str(relative)]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                                      'exported_as':str(dest.relative_to(root))}
    put(root,'state/hermes-skills.json',inventory)
    return inventory


def capture_view_definitions(conn):
    # User-created application views only. Internal/admin/foreign-server
    # definitions can carry credentials and are not part of this inventory.
    return conn.execute("""SELECT c.oid::bigint AS oid,n.nspname AS schema,c.relname AS name,
      c.relkind,pg_get_viewdef(c.oid,true) AS definition,
      coalesce((SELECT jsonb_agg(DISTINCT jsonb_build_object('schema',dn.nspname,'name',dc.relname,'kind',dc.relkind))
        FROM pg_rewrite r JOIN pg_depend d ON d.classid='pg_rewrite'::regclass AND d.objid=r.oid
        JOIN pg_class dc ON d.refclassid='pg_class'::regclass AND dc.oid=d.refobjid
        JOIN pg_namespace dn ON dn.oid=dc.relnamespace
        WHERE r.ev_class=c.oid AND dc.oid<>c.oid),'[]'::jsonb) AS dependencies
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='calliope' AND c.relkind IN ('v','m') ORDER BY c.relname""").fetchall()


def collect(options,root):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    bounds={'started_at':now(),'mode':options['mode']}; missing=[]
    begin=options.get('started_at',bounds['started_at'])
    with psycopg.connect(os.environ['WAREHOUSE_DSN'],row_factory=dict_row) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        bounds['postgres']=c.execute('SELECT now() AS captured_at,pg_current_snapshot()::text AS snapshot').fetchone()
        available={r['table_name'] for r in c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='rvbbit'")}
        def rows(table,where='',params=()):
            if table not in available:missing.append('rvbbit.'+table);return []
            return c.execute(sql.SQL('SELECT * FROM rvbbit.{} '+where).format(sql.Identifier(table)),params).fetchall()
        for table in STATE_TABLES: put(root,'state/'+table+'.json',scrub(rows(table)))
        # Safe route inventory: do not export credential-bearing backend configs.
        if 'backends' in available:
            fields=[r['column_name'] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='rvbbit' AND table_name='backends'")]
            keep=[x for x in ('name','url','transport','batch_size','max_concurrency','timeout_ms') if x in fields]
            if keep:put(root,'state/backends.json',scrub(c.execute(sql.SQL('SELECT {} FROM rvbbit.backends').format(sql.SQL(',').join(map(sql.Identifier,keep)))).fetchall()))
        inventory=c.execute("""SELECT n.nspname AS schema,c.relname AS name,c.relkind,
          c.reltuples::bigint AS estimated_rows,pg_total_relation_size(c.oid) AS bytes
          FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
          AND c.relkind IN ('r','p','m','v','f') ORDER BY 1,2""").fetchall()
        put(root,'state/relations.json',inventory)
        put(root,'state/generated-views.json',capture_view_definitions(c))
        costs=rows('cost_activity');lines(root,'costs/activity.jsonl',costs)
        put(root,'state/import_watermarks.json',rows('model_usage_import_state'))
        for table in ('memory_units','mental_models','async_operations'):
            columns=[r['column_name'] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='hindsight' AND table_name=%s ORDER BY ordinal_position",(table,))]
            keep=[x for x in columns if x not in ('embedding','search_vector','task_payload')]
            if keep:
                memories=c.execute(sql.SQL('SELECT {} FROM hindsight.{}').format(sql.SQL(',').join(map(sql.Identifier,keep)),sql.Identifier(table)))
                lines(root,'state/memory/'+table+'.jsonl',memories)
        all_sessions=rows('calliope_sessions');put(root,'state/session_index.json',all_sessions)
        selected=[r for r in all_sessions if str(r['id']) in options.get('sessions',[]) or r['hermes_session_id'] in options.get('sessions',[])]
        if options['mode']=='export' and len(selected)!=len(set(options['sessions'])):
            raise ValueError('Every supplied session must resolve to one Calliope thread')
        sids=[r['id'] for r in selected];hids={r['hermes_session_id'] for r in selected if r.get('hermes_session_id')}
        turns=rows('calliope_turns','WHERE session_id=ANY(%s)',(sids,)) if sids else []
        surfaces=rows('calliope_surfaces','WHERE session_id=ANY(%s)',(sids,)) if sids else []
        runids={r['hermes_run_id'] for r in turns if r.get('hermes_run_id')}
        if options['mode']=='export':
            linked_runs=[]
            for table in ('calliope_specialist_threads','calliope_specialist_runs'):
                related=rows(table,'WHERE source_session_id=ANY(%s)',(sids,)) if sids else []
                put(root,'thread/'+table+'.json',related)
                hids.update(r['hermes_session_id'] for r in related if r.get('hermes_session_id'))
                runids.update(r['hermes_run_id'] for r in related if r.get('hermes_run_id'))
                if table.endswith('_runs'):linked_runs.extend({'source':table,**r} for r in related)
            for parent,child,key in [('calliope_work_orders','calliope_work_order_runs','work_order_id'),('calliope_workflows','calliope_workflow_runs','workflow_id')]:
                parents=rows(parent,'WHERE source_session_id=ANY(%s)',(sids,)) if sids else []
                related=rows(child,'WHERE '+key+'=ANY(%s)',([r['id'] for r in parents],)) if parents else []
                put(root,'thread/'+parent+'.json',parents);put(root,'thread/'+child+'.json',related)
                hids.update(r['hermes_session_id'] for r in related if r.get('hermes_session_id'))
                linked_runs.extend({'source':child,**r} for r in related)
            put(root,'thread/linked-runs.json',linked_runs)
            put(root,'thread/sessions.json',selected);put(root,'thread/turns.json',turns);put(root,'thread/surfaces.json',surfaces)
            for table in WINDOW_TABLES:
                columns={r['column_name'] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='rvbbit' AND table_name=%s",(table,))}
                ts=next((x for x in ('created_at','started_at') if x in columns),None)
                if ts:lines(root,'activity/'+table+'.jsonl',rows(table,'WHERE '+ts+'>=%s',(begin,)))
            # Keep receipts and their tool results, not just cost aggregates.
            receipt_ids={r['receipt_id'] for r in costs if r.get('receipt_id') and r.get('source')=='semantic' and
                         (since(r['occurred_at'],begin) or r.get('run_id') in runids)}
            ids=[x for x in receipt_ids if re.fullmatch(r'[0-9a-fA-F-]{36}',str(x))]
            lines(root,'activity/receipts.jsonl',rows('receipts','WHERE receipt_id=ANY(%s::uuid[])',(ids,)) if ids else [])
            attachments=rows('calliope_attachments','WHERE session_id=ANY(%s)',(sids,)) if sids else []
            put(root,'thread/attachments.json',attachments)
        else: attachments=[]
        dashboards=rows('dashboards');versions=rows('dashboard_versions');deps=rows('dashboard_deps')
        put(root,'state/artifact_index.json',[{k:r[k] for k in ('id','slug','latest_version','updated_at')} for r in dashboards])
        if options['mode'] in ('export','fixture'):
            linked={s['artifact_slug'] for s in surfaces if s.get('artifact_slug')}
            changed={r['dashboard_id'] for r in versions if options['mode']=='fixture' or since(r['created_at'],begin)}
            chosen=[d for d in dashboards if d['slug'] in linked or d['id'] in changed]
            put(root,'artifacts/index.json',[{**d,'attribution':'thread' if d['slug'] in linked else 'window_only'} for d in chosen])
            for d in chosen:
                key=f"artifacts/{d['id']}-{safe_name(d['slug'])}"
                vv=[v for v in versions if v['dashboard_id']==d['id']]
                put(root,key+'/versions.json',vv);put(root,key+'/dependencies.json',[v for v in deps if v['dashboard_id']==d['id']])
                for v in vv:
                    p=root/key/str(v['version']);p.mkdir(parents=True,exist_ok=True)
                    if v.get('html'):(p/'index.html').write_text(v['html'])
                    # Preserve source file map verbatim; keys are never filesystem paths.
                    if v.get('source_files'):put(root,str(p.relative_to(root))+'/source-files.json',v['source_files'])
            lines(root,'artifacts/staged.jsonl',rows('mcp_artifacts','WHERE created_at>=%s',(begin,)))
        data_tables=options.get('tables',[])
        if options.get('public_data'):data_tables += [r['schema']+'.'+r['name'] for r in inventory if r['schema']=='public' and r['relkind'] in ('r','p')]
        data=[]
        for name in sorted(set(data_tables)):
            if not re.fullmatch(r'[A-Za-z_][\w]*\.[A-Za-z_][\w]*',name):raise ValueError('Use schema.table: '+name)
            schema,table=name.split('.')
            if not any(r['schema']==schema and r['name']==table and r['relkind'] in ('r','p','m') for r in inventory):raise ValueError('Not a local data relation: '+name)
            path=root/'data'/(name+'.csv.gz');path.parent.mkdir(exist_ok=True)
            with gzip.open(path,'wb') as f, c.cursor().copy(sql.SQL('COPY {}.{} TO STDOUT WITH (FORMAT CSV, HEADER, NULL \'\\N\')').format(sql.Identifier(schema),sql.Identifier(table))) as copy:
                for block in copy:f.write(bytes(block))
            columns=c.execute('SELECT column_name,data_type,udt_name,is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position',(schema,table)).fetchall()
            data.append({'table':name,'file':str(path.relative_to(root)),'columns':columns,'null':'\\N'})
        put(root,'data/manifest.json',data)
    # Cron is in the postgres database, NOT the rvbbit database.
    with psycopg.connect(os.environ['WAREHOUSE_DSN'],dbname='postgres',row_factory=dict_row) as c:
        c.execute('SET TRANSACTION READ ONLY')
        if c.execute("SELECT to_regclass('cron.job') AS r").fetchone()['r']:
            put(root,'state/pg_cron.json',c.execute('SELECT jobid,jobname,schedule,database,command,active FROM cron.job ORDER BY jobid').fetchall())
            put(root,'state/pg_cron_running.json',c.execute('SELECT jobid,status,start_time FROM cron.job_run_details WHERE end_time IS NULL').fetchall())
    # Read native Hermes tapes, including reasoning and compacted messages.
    native_sessions=[];native_messages=[];native_usage=[];context_chunks=[];context_originals=[]
    if options['mode']=='export':
        dbs=[Path('/opt/data/state.db'),*Path('/opt/data/profiles').glob('*/state.db')]
        snapshots=[]
        for p in dbs:
            if not p.is_file():continue
            db=sqlite3.connect(p.as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row;db.execute('BEGIN')
            names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'sessions' not in names:db.close();continue
            ss=[dict(r) for r in db.execute('SELECT * FROM sessions')]
            snapshots.append((p,db,ss,names))
        # Descendant closure spans every profile store, not just the parent store.
        changed=True
        while changed:
            old=set(hids)
            for _,_,ss,_ in snapshots:
                hids.update(r['id'] for r in ss if r.get('parent_session_id') in hids)
            changed=hids!=old
        for p,db,ss,names in snapshots:
            store=str(p.parent.relative_to('/opt/data'))
            native_sessions += [{**r,'_store':store} for r in ss if r['id'] in hids]
            for table,dest in [('messages',native_messages),('session_model_usage',native_usage)]:
                if table in names and hids:
                    dest.extend({**dict(r),'_store':store} for r in db.execute(f'SELECT * FROM {table} WHERE session_id IN ({",".join("?" for _ in hids)})',sorted(hids)))
            db.close()
            try:
                context_chunks.extend({**r,'_store':store} for r in context_index_snapshot(p.parent/'session-context-v1.sqlite',hids))
                context_originals.extend({**r,'_store':store} for r in context_index_snapshot(p.parent/'session-context-v1.sqlite',hids,originals=True))
            except sqlite3.Error as exc:
                context_chunks.append({'_store':store,'coverage_error':type(exc).__name__})
        lines(root,'thread/hermes-sessions.jsonl',native_sessions);lines(root,'thread/hermes-messages.jsonl',native_messages);lines(root,'thread/hermes-usage.jsonl',native_usage)
        lines(root,'thread/session-context-index.jsonl',context_chunks)
        lines(root,'thread/session-context-originals.jsonl',context_originals)
        events=[];diagnostics={'invalid_lines':0,'partial_lines':0,'duplicate_events':0};seen=set()
        # Freeze source byte boundaries before traversing; discover linked runs first.
        ledger=[]
        ledger_roots = [Path(os.environ.get('CALLIOPE_LEDGER_DIR', '/opt/data/calliope-ledger'))]
        if os.environ.get('CALLIOPE_MEDIA_LEDGER_DIR'):
            ledger_roots.append(Path(os.environ['CALLIOPE_MEDIA_LEDGER_DIR']))
        ledger_files = {p.resolve() for directory in ledger_roots for p in directory.glob('*.jsonl')}
        for p in sorted(ledger_files):
            size=p.stat().st_size
            with p.open('rb') as f:blob=f.read(size)
            for line in blob.splitlines(keepends=True):
                if not line.endswith(b'\n'):diagnostics['partial_lines']+=1;continue
                try:r=json.loads(line)
                except ValueError:diagnostics['invalid_lines']+=1;continue
                if r.get('session_id') in hids and r.get('run_id'):runids.add(r['run_id'])
                ledger.append(r)
        lines(root,'trace/media-window.jsonl',[{**r,'attribution':'window_only'} for r in ledger
            if r.get('event')=='media.completed' and since(r['ts'],begin)
            and r.get('run_id') not in runids and r.get('session_id') not in hids|set(sids)])
        for r in ledger:
            if r.get('run_id') not in runids and r.get('session_id') not in hids|set(sids):continue
            if r.get('event_id') in seen:diagnostics['duplicate_events']+=1;continue
            seen.add(r.get('event_id'));events.append(r)
        events.sort(key=lambda r:r.get('epoch_seconds',0));lines(root,'trace/events.jsonl',events)
        put(root,'trace/selection.json',{'run_ids':sorted(runids),'hermes_sessions':sorted(hids),'diagnostics':diagnostics})
    # Hash generated files at begin; export new/changed files at finish, plus attachments.
    before=options.get('files_before',{});files={};attachments_by_path={r['storage_path'] for r in attachments}
    for i,base in enumerate(ROOTS):
        if not base.exists():continue
        for p in base.rglob('*'):
            if p.is_symlink() or not p.is_file() or not p.resolve().is_relative_to(base.resolve()) or not file_allowed(p.relative_to(base)):continue
            key=str(p);sha=digest(p);files[key]={'sha256':sha,'bytes':p.stat().st_size}
            if options['mode']=='fixture' or (options['mode']=='export' and (before.get(key,{}).get('sha256')!=sha or key in attachments_by_path)):
                dest=root/'files'/str(i)/p.relative_to(base);dest.parent.mkdir(parents=True,exist_ok=True)
                dest.write_bytes(p.read_bytes());files[key]['exported_as']=str(dest.relative_to(root))
                if digest(dest)!=sha:files[key]['changed_during_capture']=True
    put(root,'state/files.json',files)
    capture_skills(root)
    missing_files=[p for p in attachments_by_path if not files.get(p,{}).get('exported_as')]
    # Record safe runtime flags and sanitized provider/model configs, never .env/auth stores.
    flags={k:v for k,v in os.environ.items() if k.startswith(('WAREHOUSE_MEANING_','CALLIOPE_','DOMAIN_AGENT_')) and not SECRET.search(k)}
    put(root,'state/runtime-flags.json',scrub(flags))
    import yaml
    for p in [Path('/opt/data/config.yaml'),*Path('/opt/data/profiles').glob('*/config.yaml')]:
        if p.is_file():put(root,'state/hermes-config/'+str(p.relative_to('/opt/data'))+'.json',scrub(yaml.safe_load(p.read_text())))
    for p in [Path('/opt/data/cron/jobs.json'),*Path('/opt/data/profiles').glob('*/cron/jobs.json')]:
        if p.is_file():put(root,'state/hermes-native-cron/'+str(p.relative_to('/opt/data')),scrub(json.loads(p.read_text())))
    bounds.update(finished_at=now(),missing_tables=missing,missing_attachments=missing_files,
      coverage={'screenshots':'Supply manually with --attach; no browser rendering is performed.',
                'artifacts':'Stored HTML, source file maps and dependencies; live SQL/remote assets are not made standalone.',
                'data':'Explicit table data only; calliope schema view definitions/dependencies are captured separately, not their rows.',
                'skills':'Full readable root/profile skill trees and hashes at each boundary; private authored content, credential-named files excluded.',
                'stores':'Independent PostgreSQL, cron, SQLite and filesystem snapshots; not globally atomic.',
                'configuration':'Credential fields redacted; transcripts and authored artifacts are private raw evidence.'})
    put(root,'capture.json',bounds)

if __name__=='__main__':
    options=json.load(sys.stdin)
    with tempfile.TemporaryDirectory(prefix='benchmark-capture-') as temp:
        root=Path(temp);collect(options,root)
        with tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz') as tar:
            for p in sorted(root.rglob('*')):
                if p.is_file():tar.add(p,arcname=str(p.relative_to(root)),recursive=False)
