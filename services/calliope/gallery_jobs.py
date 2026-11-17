"""Admin job observability. No SQL/results or source credentials cross this API."""
from functools import lru_cache
import json
import uuid

import calliope
import workspace_sql


def require_admin(factory, owner):
    if not calliope.calliope_dreams.is_company_admin(factory, owner):
        raise PermissionError("Jobs inventory requires a Calliope administrator")


def describe(conn, kind, key, description):
    if description is None:
        return
    if not isinstance(description, str) or len(description) > 1000:
        raise ValueError("description must be at most 1000 characters")
    conn.execute("INSERT INTO rvbbit.calliope_job_descriptions(kind,job_key,description) VALUES(%s,%s,%s) "
                 "ON CONFLICT(kind,job_key) DO UPDATE SET description=excluded.description,updated_at=now()",
                 (kind, str(key), description.strip()))


@lru_cache(maxsize=512)
def relations(sql):
    try:
        plan = workspace_sql.plan(sql)
        return {"sources": [f'{s}.{t}' for s, t in plan['reads']],
                "destinations": [f'{s}.{t}' for s, t in plan['writes']],
                "functions": ['.'.join(f) for f in plan['operators']]}
    except ValueError:
        return {"sources": [], "destinations": [], "functions": []}


def _run(row):
    # Errors may quote SQL, values or remote credentials. Expose a useful category;
    # Calliope can inspect the full, access-controlled execution receipt on request.
    error = str(row.get('error') or '')
    reason = ('Permission denied' if 'permission denied' in error.lower() else
              'Execution timed out' if any(x in error.lower() for x in ['timeout', 'time out', 'deadline']) else
              'Execution failed — ask Calliope to inspect the receipt') if error else None
    return {k: row.get(k) for k in ['id', 'status', 'started_at', 'finished_at', 'duration_ms', 'rows_loaded', 'rows_affected', 'revision']} | {'error': reason}


def snapshot(factory, owner, ref=None):
    require_admin(factory, owner)
    if ref and (not isinstance(ref, str) or not ref.startswith(('sql:', 'mirror:'))):
        raise ValueError('Invalid job reference')
    items = []
    with factory() as conn, conn.transaction():
        conn.execute("SET LOCAL statement_timeout='8s'")
        sql_jobs = conn.execute("""SELECT j.*, d.description,
          coalesce(h.runs,'[]'::jsonb) AS history,
          (SELECT max(finished_at) FROM rvbbit.calliope_workspace_job_runs WHERE job_id=j.id AND status='complete') AS last_success
          FROM rvbbit.calliope_workspace_jobs j
          LEFT JOIN rvbbit.calliope_job_descriptions d ON d.kind='sql' AND d.job_key=j.id::text
          LEFT JOIN LATERAL (SELECT jsonb_agg(r ORDER BY r.started_at DESC) AS runs FROM (
            SELECT r.id,r.revision,r.status,r.started_at,r.finished_at,r.error,
              extract(epoch FROM (r.finished_at-r.started_at))*1000 AS duration_ms,
              (SELECT sum((s->>'rows_affected')::bigint) FROM jsonb_array_elements(coalesce(a.result->'statements','[]'::jsonb)) s
                WHERE s->>'status' ~ '^(INSERT|UPDATE|DELETE|MERGE) ') AS rows_affected
            FROM rvbbit.calliope_workspace_job_runs r LEFT JOIN rvbbit.calliope_action_runs a ON a.id=r.action_run_id
            WHERE r.job_id=j.id ORDER BY r.started_at DESC LIMIT 24) r) h ON true
          WHERE (%s::text IS NULL OR 'sql:'||j.id::text=%s) ORDER BY j.name,j.id LIMIT 501""", (ref, ref)).fetchall()
        for j in sql_jobs:
            history = [_run(r) for r in j['history']]
            lineage = relations(j['sql'])
            items.append(dict(ref='sql:'+str(j['id']), kind='sql', name=j['name'], owner=j['owner_email'],
                              description=j['description'] or ('Updates '+', '.join(lineage['destinations'][:3]) if lineage['destinations'] else 'Runs a saved SQL task in the Calliope workspace.'),
                              description_source='authored' if j['description'] else 'configuration',
                              enabled=j['enabled'], interval_seconds=j['interval_seconds'], next_run_at=j['next_run_at'],
                              last_success=j['last_success'], revision=j['revision'], queued=j['run_requested'], history=history, **lineage))
        exists = conn.execute("SELECT to_regclass('rvbbit.mirror_jobs') IS NOT NULL AS ready").fetchone()['ready']
        if exists:
            mirrors = conn.execute("""SELECT j.*, d.description,coalesce(h.runs,'[]'::jsonb) AS history,
              coalesce(t.mappings,'[]'::jsonb) AS mappings,
              (SELECT max(finished_at) FROM rvbbit.mirror_runs WHERE job_name=j.job_name AND status='succeeded') AS last_success
              FROM rvbbit.mirror_jobs j LEFT JOIN rvbbit.calliope_job_descriptions d ON d.kind='mirror' AND d.job_key=j.job_name
              LEFT JOIN LATERAL (SELECT jsonb_agg(r ORDER BY r.requested_at DESC) AS runs FROM (
                SELECT run_id AS id,status,requested_at,started_at,finished_at,rows_loaded,error_message AS error,
                  extract(epoch FROM (finished_at-started_at))*1000 AS duration_ms
                FROM rvbbit.mirror_runs WHERE job_name=j.job_name ORDER BY requested_at DESC LIMIT 24) r) h ON true
              LEFT JOIN LATERAL (SELECT jsonb_agg(jsonb_build_object('source',j.source_schema||'.'||source_table,
                'destination',j.destination_schema||'.'||destination_table)) AS mappings
                FROM rvbbit.mirror_tables WHERE job_name=j.job_name AND enabled) t ON true
              WHERE (%s::text IS NULL OR 'mirror:'||j.job_name=%s) ORDER BY j.job_name LIMIT 501""", (ref, ref)).fetchall()
            for j in mirrors:
                items.append(dict(ref='mirror:'+j['job_name'], kind='mirror', name=j['job_name'], owner=j['created_by'],
                                  description=j['description'] or f"Mirrors {j['connection_name']} / {j['source_schema']} into {j['destination_schema']}.",
                                  description_source='authored' if j['description'] else 'configuration',
                                  source_connection=j['connection_name'], sources=[m['source'] for m in j['mappings']],
                                  destinations=[m['destination'] for m in j['mappings']], functions=[], enabled=j['enabled'],
                                  interval_seconds=j['schedule_seconds'] or 0, next_run_at=j['next_run_at'],
                                  last_success=j['last_success'], history=[_run(r) for r in j['history']]))
    for j in items:
        latest = j['history'][0] if j['history'] else {}
        active = latest.get('status') in {'running', 'extracting', 'loading', 'publishing'}
        failed = latest.get('status') in {'failed', 'partial', 'interrupted', 'cancelled'}
        queued = j.get('queued') or latest.get('status') in {'queued', 'claimed'}
        j['status'] = 'running' if active else 'queued' if queued else 'failed' if failed else 'scheduled' if j['enabled'] and j['interval_seconds'] else 'manual' if not j['interval_seconds'] else 'paused'
        j['can_resume'] = not j['enabled'] and bool(j['interval_seconds'])
    if ref and not items:
        raise LookupError('Job no longer exists')
    return json.loads(json.dumps({'jobs': items[:500], 'truncated': len(items)>500}, default=str))


def control(factory, owner, ref, operation):
    require_admin(factory, owner)
    if operation not in {'pause','resume'}:
        raise ValueError('Only pause or resume is available here')
    if not isinstance(ref, str) or ':' not in ref:
        raise ValueError('Invalid job reference')
    kind, key = ref.split(':',1)
    if kind not in {'sql','mirror'}:
        raise ValueError('Invalid job kind')
    if kind == 'sql':
        key = str(uuid.UUID(key))
    with factory() as conn, conn.transaction():
        conn.execute("SET LOCAL lock_timeout='3s'")
        if kind == 'sql':
            job = conn.execute('SELECT * FROM rvbbit.calliope_workspace_jobs WHERE id=%s FOR UPDATE', (key,)).fetchone()
        else:
            job = conn.execute('SELECT * FROM rvbbit.mirror_jobs WHERE job_name=%s FOR UPDATE', (key,)).fetchone()
        if not job:
            raise LookupError('Job no longer exists')
        interval = job['interval_seconds'] if kind == 'sql' else job['schedule_seconds']
        if operation == 'resume':
            if not interval:
                raise ValueError('Manual job has no schedule. Ask Calliope to set one.')
            if kind == 'sql' and not calliope.calliope_dreams.is_company_admin(factory, job['owner_email']):
                raise ValueError('The job owner no longer has execution authority. Ask Calliope to resolve ownership.')
        enabled = operation == 'resume'
        changed = bool(job['enabled']) != enabled or (kind == 'sql' and not enabled and job['run_requested'])
        if changed:
            if kind == 'sql':
                conn.execute("UPDATE rvbbit.calliope_workspace_jobs SET enabled=%s,run_requested=false,"
                             "next_run_at=CASE WHEN %s THEN now()+make_interval(secs=>interval_seconds) END,updated_at=now() WHERE id=%s", (enabled,enabled,key))
            else:
                conn.execute("UPDATE rvbbit.mirror_jobs SET enabled=%s,next_run_at=CASE WHEN %s THEN now()+make_interval(secs=>schedule_seconds) END,"
                             "updated_at=now(),updated_by=%s WHERE job_name=%s",(enabled,enabled,owner,key))
            conn.execute("INSERT INTO rvbbit.calliope_job_controls(actor,kind,job_key,operation,previous_enabled) VALUES(%s,%s,%s,%s,%s)",
                         (owner,kind,key,operation,job['enabled']))
    return {'ok': True, 'changed': changed, 'note': 'Pause stops future scheduling; work already queued or running may finish.'}
