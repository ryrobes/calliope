"""System projection of the SQL operator test harness, never a second test catalog.

Each operator commits to operator_test_runs independently. A small batch record
holds selection/configuration and progress; no test SQL is accepted from the UI.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.operator_test_batches (
 run_id bigint PRIMARY KEY, owner_email text NOT NULL, session_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz,
 status text NOT NULL, scope text NOT NULL, plan jsonb NOT NULL,
 active_operator text, message text
);
CREATE UNIQUE INDEX IF NOT EXISTS operator_test_one_active_batch
 ON rvbbit.operator_test_batches ((true)) WHERE status IN ('queued','running');
CREATE TABLE IF NOT EXISTS rvbbit.operator_test_observations (
 run_id bigint NOT NULL REFERENCES rvbbit.operator_test_batches(run_id),
 operator text NOT NULL, status text NOT NULL, duration_ms double precision,
 detail text, configuration jsonb NOT NULL,
 PRIMARY KEY(run_id,operator)
);
"""
LOCK = 731948203


def ready(conn):
    return bool(conn.execute("SELECT to_regclass('rvbbit.operator_availability') IS NOT NULL "
                            "AND to_regclass('rvbbit.operator_test_runs') IS NOT NULL AS ok").fetchone()['ok'])


def catalog(conn):
    rows = [dict(r) for r in conn.execute("""SELECT a.*,o.model,o.tests,o.steps,o.opts_default,
      md5((to_jsonb(o)-'created_at'-'updated_at')::text) AS revision
      FROM rvbbit.operator_availability a JOIN rvbbit.operators o ON o.name=a.operator
      ORDER BY a.operator""").fetchall()]
    default = conn.execute('SELECT rvbbit.default_provider() AS name').fetchone()['name']
    backends = {r['name']:dict(r) for r in conn.execute("""SELECT name,source_model,
      transport_opts FROM rvbbit.backends""").fetchall()}
    for item in rows:
        item['execution'] = execution_models(item, default, backends)
        # Prompts/inputs are unnecessary in the checks projection; never copy backend secrets.
        item.pop('steps');item.pop('opts_default')
    return rows


def execution_models(item, default_provider, backends):
    """Configured execution, not a claim about a model observed on a test call.

    Mirror single-call vs pipeline model precedence. A specialist's model comes
    from its backend; the operator's generic LLM default is irrelevant to it.
    """
    steps = item.get('steps')
    if steps is None:
        steps = [{'kind':'llm','name':'main',
                  'model':(item.get('opts_default') or {}).get('model',item.get('model'))}]
    result = []
    def visit(nodes):
        for step in nodes:
            if not isinstance(step,dict):continue
            kind = step.get('kind')
            if kind == 'llm':
                provider = step.get('provider') or default_provider
                backend = backends.get(provider,{})
                opts = backend.get('transport_opts') or {}
                requested = str(step.get('model',item.get('model')) or '').strip()
                model = requested or opts.get('model')
                route = opts.get('clover_route') or {}
                if opts.get('model_policy') == 'managed':
                    canonical = opts.get('model')
                    if not requested or requested == canonical or requested in opts.get('model_aliases',[]):
                        model = route.get('model_version') or backend.get('source_model') or canonical
                    # An alternate managed alias is its own route. Never attribute
                    # the canonical route's upstream model to that different alias.
                result.append({'step':step.get('name','LLM'),'kind':kind,'provider':provider,
                               'model':model or 'Not configured','source':route.get('source')})
            elif kind in ('specialist','embed'):
                provider = step.get('specialist') or ('embed' if kind=='embed' else '')
                backend = backends.get(provider,{})
                route = (backend.get('transport_opts') or {}).get('clover_route') or {}
                result.append({'step':step.get('name',provider),'kind':kind,'provider':provider,
                    'model':route.get('model_version') or backend.get('source_model') or 'Not recorded',
                    'source':route.get('source')})
            for value in step.values():
                if isinstance(value,list):visit(value)
                elif isinstance(value,dict):visit([value])
    visit(steps)
    return result


def unavailable(item):
    if not item['callable']:
        return 'Needs setup: ' + '; '.join(d['backend'] + ' — ' + ((d.get('route') or {}).get('reason') or 'service is not enabled or installed') for d in item['dependencies'] if not d['callable'])
    return None


def start(factory, owner, session_id, operator=None):
    with factory() as c, c.transaction():
        if not ready(c):
            raise ValueError('Install the SQL operator availability migration before running checks.')
        # Serialize creation across server processes; one batch uses the SQL harness at a time.
        c.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK+1,))
        active=c.execute("SELECT run_id FROM rvbbit.operator_test_batches WHERE status IN ('queued','running')").fetchone()
        if active:
            run_id=active['run_id']
        else:
            plan=catalog(c)
            if operator is not None:
                plan=[r for r in plan if r['operator']==operator]
                if not plan or not plan[0]['test_count']:
                    raise ValueError('Choose an installed operator with embedded tests.')
            plan=[r for r in plan if r['test_count']]
            if not plan:
                raise ValueError('No embedded tests are installed.')
            run_id=c.execute("SELECT nextval('rvbbit.operator_test_run_seq') AS id").fetchone()['id']
            c.execute("INSERT INTO rvbbit.operator_test_batches(run_id,owner_email,session_id,status,scope,plan) VALUES(%s,%s,%s,'queued',%s,%s::jsonb)",
                      (run_id,owner,session_id,operator or 'supported',json.dumps(plan)))
    launch(factory,run_id)
    return {'run_id':run_id}


def launch(factory, run_id):
    threading.Thread(target=execute, args=(factory,run_id), daemon=True, name='system-sql-checks').start()


def execute(factory, run_id):
    # The connection-scoped lock is also a liveness check. A dead worker leaves
    # its committed results intact; we report interruption rather than replaying SQL.
    with factory() as c:
        if not c.execute('SELECT pg_try_advisory_lock(%s) AS locked',(LOCK,)).fetchone()['locked']:
            return
        try:
            batch=c.execute('SELECT * FROM rvbbit.operator_test_batches WHERE run_id=%s',(run_id,)).fetchone()
            if not batch or batch['status'] not in ('queued','running'):
                return
            if batch['status']=='running':
                c.execute("UPDATE rvbbit.operator_test_batches SET status='interrupted',finished_at=now(),message='Worker stopped; completed results are retained. Start a new run to retry.' WHERE run_id=%s",(run_id,))
                return
            c.execute("UPDATE rvbbit.operator_test_batches SET status='running' WHERE run_id=%s",(run_id,))
            for item in batch['plan']:
                op=item['operator'];begin=time.monotonic()
                c.execute('UPDATE rvbbit.operator_test_batches SET active_operator=%s WHERE run_id=%s',(op,run_id))
                current=next((r for r in catalog(c) if r['operator']==op),None)
                reason=('Definition changed after this run was queued.' if not current or current['revision']!=item['revision']
                        else 'Backend configuration changed after this run was queued.' if current['dependencies']!=item['dependencies'] or current.get('execution')!=item.get('execution')
                        else unavailable(current))
                status='skipped' if reason else 'complete'
                if not reason:
                    notices=[]
                    def notice(diagnostic):
                        message=diagnostic.message_primary or ''
                        if message and message not in notices and len(notices)<12:
                            notices.append(message[:1500])
                    c.add_notice_handler(notice)
                    try:
                        with c.transaction():
                            c.execute("SET LOCAL statement_timeout='180s'")
                            # This is the existing SQL harness, including its cache-bypass behavior.
                            c.execute("""INSERT INTO rvbbit.operator_test_runs
                              (run_id,operator,test_name,passed,actual,expected,description,error,backend_tag)
                              SELECT %s,%s,test_name,passed,actual,expected,description,error,%s
                              FROM rvbbit.run_tests(%s)""",(run_id,op,'system:'+item['revision'],op))
                    except Exception as exc:
                        status='error';reason=type(exc).__name__ + ': ' + str(exc)[:800]
                        c.execute("""INSERT INTO rvbbit.operator_test_runs(run_id,operator,test_name,passed,error,backend_tag)
                          VALUES(%s,%s,'<harness error>',false,%s,%s)""",(run_id,op,reason,'system:'+item['revision']))
                    finally:
                        c.remove_notice_handler(notice)
                    if notices:
                        reason=((reason+'\n') if reason else '')+'SQL warnings from this function run:\n'+'\n'.join(notices)
                c.execute("""INSERT INTO rvbbit.operator_test_observations(run_id,operator,status,duration_ms,detail,configuration)
                  VALUES(%s,%s,%s,%s,%s,%s::jsonb)""",(run_id,op,status,(time.monotonic()-begin)*1000,reason,json.dumps(item)))
            c.execute("UPDATE rvbbit.operator_test_batches SET status='complete',finished_at=now(),active_operator=NULL WHERE run_id=%s",(run_id,))
        except Exception as exc:
            c.execute("UPDATE rvbbit.operator_test_batches SET status='interrupted',finished_at=now(),message=%s WHERE run_id=%s",(type(exc).__name__,run_id))
        finally:
            c.execute('SELECT pg_advisory_unlock(%s)',(LOCK,))


def read(factory, run_id=None, operator=None):
    with factory() as c:
        if not ready(c):
            return {'available':False,'operators':[],'runs':[], 'message':'SQL operator availability migration is not installed.'}
        operators=catalog(c)
        runs=[dict(r) for r in c.execute("""WITH ids AS (
          SELECT run_id FROM rvbbit.operator_test_batches UNION SELECT run_id FROM rvbbit.operator_test_runs
        ), recent AS (SELECT run_id FROM ids ORDER BY run_id DESC LIMIT 12)
        SELECT i.run_id,coalesce(b.created_at,min(t.ts)) AS created_at,coalesce(b.status,'complete') AS status,
          b.scope,b.active_operator,b.message, count(t.*) AS total,count(*) FILTER(WHERE t.passed) AS passed,
          (SELECT count(*) FROM rvbbit.operator_test_observations o WHERE o.run_id=i.run_id AND status='skipped') AS skipped
        FROM recent i LEFT JOIN rvbbit.operator_test_batches b USING(run_id)
        LEFT JOIN rvbbit.operator_test_runs t USING(run_id)
        GROUP BY i.run_id,b.created_at,b.status,b.scope,b.active_operator,b.message ORDER BY i.run_id DESC""").fetchall()]
        active=next((r for r in runs if r['status'] in ('queued','running')),None)
        # A click on an operator means its latest observation, not the newest
        # unrelated one-operator batch. Explicit history cells remain exact.
        if run_id:
            run_id=int(run_id)
        elif operator:
            found=c.execute("""SELECT max(run_id) AS run_id FROM (
              SELECT run_id FROM rvbbit.operator_test_runs WHERE operator=%s
              UNION SELECT run_id FROM rvbbit.operator_test_observations WHERE operator=%s
              UNION SELECT run_id FROM rvbbit.operator_test_batches b WHERE EXISTS
                (SELECT 1 FROM jsonb_array_elements(b.plan) p WHERE p->>'operator'=%s)
            ) relevant""",(operator,operator,operator)).fetchone()
            run_id=found['run_id']
        else:
            run_id=active['run_id'] if active else (runs[0]['run_id'] if runs else None)
        selected_run=next((r for r in runs if r['run_id']==run_id),None)
        if run_id is not None and selected_run is None:
            selected_run=c.execute("""SELECT %s::bigint AS run_id,coalesce(b.created_at,min(t.ts)) AS created_at,
              coalesce(b.status,'complete') AS status,b.scope,b.active_operator,b.message,
              count(t.*) AS total,count(*) FILTER(WHERE t.passed) AS passed,
              (SELECT count(*) FROM rvbbit.operator_test_observations WHERE run_id=%s AND status='skipped') AS skipped
              FROM (SELECT %s::bigint AS run_id) i LEFT JOIN rvbbit.operator_test_batches b USING(run_id)
              LEFT JOIN rvbbit.operator_test_runs t USING(run_id)
              GROUP BY b.created_at,b.status,b.scope,b.active_operator,b.message
              HAVING b.created_at IS NOT NULL OR count(t.*)>0""",(run_id,run_id,run_id)).fetchone()
            if selected_run is None: raise ValueError('Unknown test run.')
            selected_run=dict(selected_run)
        if not operator:
            operator=(selected_run or {}).get('active_operator')
            if not operator and run_id:
                found=c.execute("""SELECT operator FROM (
                  SELECT operator,0 AS priority FROM rvbbit.operator_test_runs WHERE run_id=%s
                  UNION SELECT operator,1 FROM rvbbit.operator_test_observations WHERE run_id=%s
                  UNION SELECT p->>'operator',2 FROM rvbbit.operator_test_batches b,
                    LATERAL jsonb_array_elements(b.plan) p WHERE b.run_id=%s
                ) candidates ORDER BY priority,operator LIMIT 1""",(run_id,run_id,run_id)).fetchone()
                operator=found['operator'] if found else None
            if not operator:
                operator=next((o['operator'] for o in operators if o['test_count']),None)
        matrix=[dict(r) for r in c.execute("""SELECT run_id,operator,count(*) AS total,count(*) FILTER(WHERE passed) AS passed,
          count(*) FILTER(WHERE error IS NOT NULL) AS errors FROM rvbbit.operator_test_runs
          WHERE run_id=ANY(%s) GROUP BY run_id,operator""",(list({r['run_id'] for r in runs} | ({run_id} if run_id else set())),)).fetchall()]
        observations=[dict(r) for r in c.execute('SELECT run_id,operator,status,duration_ms,detail FROM rvbbit.operator_test_observations WHERE run_id=ANY(%s)',(list({r['run_id'] for r in runs} | ({run_id} if run_id else set())),)).fetchall()]
        for observation in observations:
            reason = observation.get('detail') or ''
            observation['display_state'] = ('needs_setup' if reason.startswith(('Needs setup:', 'Requires configuration:')) else
                'changed' if 'changed after this run was queued' in reason else
                'excluded' if observation['status']=='skipped' else observation['status'])
        item=next((r for r in operators if r['operator']==operator),None)
        detail=None
        if operator:
            saved=c.execute('SELECT configuration FROM rvbbit.operator_test_observations WHERE run_id=%s AND operator=%s',(run_id,operator)).fetchone()
            # While in flight, frozen definitions also remain available in the batch plan.
            if not saved and run_id:
                saved=c.execute("SELECT p AS configuration FROM rvbbit.operator_test_batches b,LATERAL jsonb_array_elements(b.plan) p WHERE run_id=%s AND p->>'operator'=%s",(run_id,operator)).fetchone()
            if not item and not saved: raise ValueError('Unknown operator.')
            results=[dict(r) for r in c.execute('SELECT test_name,passed,actual,expected,error,description FROM rvbbit.operator_test_runs WHERE run_id=%s AND operator=%s ORDER BY ts,test_name',(run_id,operator)).fetchall()]
            observation=next((o for o in observations if o['run_id']==run_id and o['operator']==operator),None)
            state=(observation['status'] if observation else 'complete' if results else
                   'running' if saved and selected_run and selected_run.get('active_operator')==operator and selected_run['status']=='running' else
                   'queued' if saved and selected_run and selected_run['status'] in ('queued','running') else
                   'interrupted' if saved and selected_run and selected_run['status']=='interrupted' else
                   'not_in_run' if run_id else 'never_run')
            detail={'state':state, 'run_id':run_id, 'observation':observation,
                    'definition': saved['configuration'] if saved else dict(item),
                    'definition_at_run': bool(saved),
                    'current_execution':item.get('execution') if item else None,
                    'results':results}
        for item in operators:
            item.pop('tests',None)
        result={'available':True,'observed_at':datetime.now(timezone.utc).isoformat(),
                'operators':operators,'runs':runs,'matrix':matrix,'observations':observations,
                'run_id':run_id,'selected_run':selected_run,'operator':operator,'detail':detail}
    if active: launch(factory,active['run_id'])
    return json.loads(json.dumps(result,default=str))


def detail_query(run_id, operator=None):
    rid=int(run_id)
    if rid<1: raise ValueError('Choose a test run.')
    predicate='t.run_id='+str(rid)
    if operator:
        predicate+=" AND t.operator='"+str(operator).replace("'","''")+"'"
    return {'title':'SQL checks · run '+str(rid), 'sql':"SELECT t.*,o.duration_ms,o.detail AS function_diagnostics,o.configuration->'dependencies' AS routes FROM rvbbit.operator_test_runs t LEFT JOIN rvbbit.operator_test_observations o USING(run_id,operator) WHERE "+predicate+' ORDER BY t.operator,t.test_name'}


def discovery(conn, rows):
    names=[r['name'].removeprefix('rvbbit.') for r in rows if r['kind']=='cap_operator']
    if not names or not conn.execute("SELECT to_regclass('rvbbit.operator_availability') IS NOT NULL AS ok").fetchone()['ok']:
        return rows
    facts={r['operator']:dict(r) for r in conn.execute('SELECT * FROM rvbbit.operator_availability WHERE operator=ANY(%s)',(names,)).fetchall()}
    # Do not advertise dead SQL calls as tools. Keep installable packs in the
    # catalog, and keep SQL definitions/tests intact for repair and inspection.
    # Recent System checks also suppress currently broken routes, without
    # permanently disabling an installed capability after a transient outage.
    unhealthy = set()
    if conn.execute("SELECT to_regclass('rvbbit.calliope_system_snapshots') IS NOT NULL AS ok").fetchone()['ok']:
        observed = conn.execute("""SELECT payload->'capabilities' AS capabilities
            FROM rvbbit.calliope_system_snapshots
            WHERE observed_at>now()-interval '2 minutes' AND payload->>'probed'='true'
              AND jsonb_typeof(payload->'capabilities')='array'
            ORDER BY observed_at DESC LIMIT 1""").fetchone()
        if observed:
            unhealthy = {c['id'] for c in observed['capabilities']
                         if c.get('state') in ('unavailable','missing_credentials','disabled')}
    result=[]
    for row in rows:
        fact=facts.get(row['name'].removeprefix('rvbbit.')) if row['kind']=='cap_operator' else None
        if fact:
            if not fact['callable'] or any((d.get('route') or {}).get('id') in unhealthy for d in fact.get('dependencies', [])):
                continue
            row={**row,'sql_availability':fact}
        result.append(row)
    return result
