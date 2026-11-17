"""Versioned HTML app commands. No agent invocation or approval workflow."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from threading import Lock
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pglast import parse_sql
from pglast.parser import parse_sql_json
from pglast.stream import RawStream
from pglast.visitors import Visitor

import workspace_sql

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.app_action_runs (
 id uuid PRIMARY KEY, slug text NOT NULL, app_version integer NOT NULL,
 action text NOT NULL, actor text NOT NULL, request_key uuid NOT NULL,
 input_hash text NOT NULL, status text NOT NULL,
 result jsonb NOT NULL, elapsed_ms integer NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS app_action_completed_request
 ON rvbbit.app_action_runs(slug,actor,request_key) WHERE status='complete';
CREATE INDEX IF NOT EXISTS app_action_recent ON rvbbit.app_action_runs(slug,created_at DESC);
REVOKE ALL ON rvbbit.app_action_runs FROM PUBLIC;
CREATE TABLE IF NOT EXISTS rvbbit.app_action_external_attempts (
 slug text NOT NULL, actor text NOT NULL, request_key uuid NOT NULL,
 input_hash text NOT NULL, run_id uuid NOT NULL, app_version integer NOT NULL,
 action text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(slug,actor,request_key)
);
REVOKE ALL ON rvbbit.app_action_external_attempts FROM PUBLIC;
"""
_lock = Lock()
_prepared = {}


class ActionError(ValueError):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


class Parameters(Visitor):
    def __init__(self):
        super().__init__()
        self.indices = []

    def visit_ParamRef(self, ancestors, node):
        index = node.number - 1
        if index not in self.indices:
            self.indices.append(index)
        node.number = self.indices.index(index) + 1


def statement_parameters(statement):
    tree = parse_sql(statement)
    visitor = Parameters()
    visitor(tree)
    return RawStream()(tree), visitor.indices


def actor_parameter(value):
    return isinstance(value, dict) and set(value) == {'actor'} and value['actor'] is True


def actor_value(actor):
    if not isinstance(actor, str) or not actor.strip():
        raise ActionError('UNAUTHORIZED', 'A signed-in user is required for this SQL binding', 401)
    return actor


def normalize(actions):
    if not isinstance(actions, dict) or len(actions) > 40:
        raise ValueError('manifest.actions must be an object with at most 40 named SQL actions')
    result = {}
    for name, action in actions.items():
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) or not isinstance(action, dict):
            raise ValueError('Action names must be lowercase identifiers')
        if set(action) - {'description', 'inputs', 'steps', 'external_effects'}:
            raise ValueError(f'{name}: use description, inputs, steps, and optional external_effects')
        if action.get('external_effects', 'may_write') not in {'read_only', 'may_write'}:
            raise ValueError('external_effects must be read_only or may_write')
        inputs = action.get('inputs', {})
        if not isinstance(inputs, dict) or len(inputs) > 32:
            raise ValueError(f'{name}: inputs must declare at most 32 parameters')
        for key, definition in inputs.items():
            if not isinstance(key, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', key) or not isinstance(definition, dict):
                raise ValueError(f'{name}: invalid input definition')
            if set(definition) - {'type', 'enum', 'max_length', 'nullable'}:
                raise ValueError(f'{name}.{key}: unsupported input option')
            if definition.get('type') not in {'string', 'integer', 'number', 'boolean', 'uuid', 'json'}:
                raise ValueError(f'{name}.{key}: declare a supported type')
            if 'nullable' in definition and type(definition['nullable']) is not bool:
                raise ValueError('nullable must be boolean')
            if 'max_length' in definition and (type(definition['max_length']) is not int or not 1 <= definition['max_length'] <= 100000):
                raise ValueError('max_length must be 1–100000')
            if 'enum' in definition and (not isinstance(definition['enum'], list) or not 1 <= len(definition['enum']) <= 100):
                raise ValueError('enum must contain 1–100 values')
        steps = action.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= 12:
            raise ValueError(f'{name}: supply 1–12 SQL steps')
        all_sql = []
        for step in steps:
            if not isinstance(step, dict) or set(step) - {'sql', 'params', 'expect_rows'}:
                raise ValueError(f'{name}: steps support sql, params, expect_rows')
            statement = step.get('sql', '')
            batch = workspace_sql.plan(statement)
            if len(batch['statements']) != 1:
                raise ValueError('Each app step must be one SQL statement')
            tree = json.loads(parse_sql_json(statement))
            if any(k[:1].isupper() and k.endswith('Stmt') and k not in {'SelectStmt','InsertStmt','UpdateStmt','DeleteStmt','MergeStmt'}
                   for k, v in workspace_sql._walk(tree)):
                raise ValueError('App actions support data changes and reads; create tables with run_workspace_sql')
            params = step.get('params', [])
            if not isinstance(params, list) or any(not actor_parameter(p) and (not isinstance(p, str) or p not in inputs) for p in params):
                raise ValueError('Step params must name declared inputs or {"actor":true} in $1, $2 order')
            _, indices = statement_parameters(statement)
            if any(i < 0 or i >= len(params) for i in indices):
                raise ValueError('SQL parameter does not have a matching step params entry')
            if 'expect_rows' in step and (type(step['expect_rows']) is not int or not 0 <= step['expect_rows'] <= 10000):
                raise ValueError('expect_rows must be an integer between 0 and 10000')
            all_sql.append(statement)
        combined = workspace_sql.plan(';\n'.join(s.rstrip().rstrip(';') for s in all_sql))
        if not combined['writes'] and not combined['operators']:
            raise ValueError('Use rvbbitQuery for plain reads; actions must write workspace data or invoke registered operators')
        result[name] = {**action, 'inputs': inputs}
    return result


def values_for(action, inputs):
    if not isinstance(inputs, dict) or set(inputs) != set(action['inputs']):
        raise ActionError('INVALID_INPUT', 'Supply exactly the declared action inputs')
    if len(json.dumps(inputs, allow_nan=False)) > 128000:
        raise ActionError('INVALID_INPUT', 'Action inputs exceed 128 KB')
    for name, definition in action['inputs'].items():
        value = inputs[name]
        if value is None and definition.get('nullable') is True:
            continue
        kind = definition['type']
        valid = {'string': isinstance(value, str), 'integer': type(value) is int,
                 'number': type(value) in (int, float), 'boolean': type(value) is bool,
                 'uuid': isinstance(value, str), 'json': value is not None}[kind]
        if not valid:
            raise ActionError('INVALID_INPUT', f'{name} must be {kind}')
        if isinstance(value, str) and len(value) > definition.get('max_length', 10000):
            raise ActionError('INVALID_INPUT', f'{name} is too long')
        if 'enum' in definition and value not in definition['enum']:
            raise ActionError('INVALID_INPUT', f'{name} is not an allowed value')
        if kind == 'uuid':
            try: uuid.UUID(value)
            except ValueError: raise ActionError('INVALID_INPUT', f'{name} must be a UUID') from None
    return {k: Jsonb(v) if action['inputs'][k]['type'] == 'json' and v is not None else v
            for k, v in inputs.items()}


def _prepare(factory, batch, seed, schema_allowed):
    # Prepare the existing limited workspace login once per plan per minute,
    # rather than doing role/catalog housekeeping on every UI click. Operator
    # plans always revalidate live registration and populate current adapters.
    key = (factory, hashlib.sha256(seed.encode()).hexdigest(), batch['sql_sha256'])
    with _lock:
        cached = _prepared.get(key)
        if not batch['operators'] and cached and cached[0] > time.monotonic():
            return cached[1:]
        dsn, role = workspace_sql._prepare(factory, batch, seed, schema_allowed)
        with factory() as conn:
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('app-action-schema',0))")
                conn.execute(DDL)
                conn.execute(sql.SQL('GRANT USAGE ON SCHEMA rvbbit TO {}').format(sql.Identifier(role)))
                conn.execute(sql.SQL('GRANT SELECT,INSERT ON rvbbit.app_action_runs TO {}').format(sql.Identifier(role)))
        if len(_prepared) > 256:
            _prepared.clear()
        if not batch['operators']:
            _prepared[key] = (time.monotonic() + 60, dsn, role)
        return dsn, role


def execute(factory, *, slug, version, name, actor, request_key, manifest, inputs, seed, schema_allowed):
    try:
        request_key = str(uuid.UUID(str(request_key)))
    except ValueError:
        raise ActionError('INVALID_REQUEST_KEY', 'Supply a UUID idempotency key') from None
    action = normalize(manifest.get('actions', {})).get(name)
    if not action:
        raise ActionError('ACTION_NOT_FOUND', 'No such action in this app version', 404)
    values = values_for(action, inputs)
    fingerprint = hashlib.sha256(json.dumps([version, name, action, inputs], sort_keys=True, allow_nan=False).encode()).hexdigest()
    statements = [s['sql'].rstrip().rstrip(';') for s in action['steps']]
    batch = workspace_sql.plan(';\n'.join(statements))
    dsn, role = _prepare(factory, batch, seed, schema_allowed)
    external_guard = bool(batch['operators']) and action.get('external_effects', 'may_write') != 'read_only'
    external_started = False
    started = time.monotonic()
    run_id = str(uuid.uuid4())
    def record(conn, status, result):
        conn.execute('INSERT INTO rvbbit.app_action_runs(id,slug,app_version,action,actor,request_key,input_hash,status,result,elapsed_ms) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                     (run_id,slug,version,name,actor,request_key,fingerprint,status,Jsonb(result),int((time.monotonic()-started)*1000)))
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            conn.execute("SELECT set_config('search_path','pg_catalog,calliope,calliope_apps',true), set_config('statement_timeout','15000',true), set_config('lock_timeout','5000',true), set_config('work_mem','16MB',true)")
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (json.dumps([slug,actor,request_key]),))
            previous = conn.execute("SELECT input_hash,result FROM rvbbit.app_action_runs WHERE slug=%s AND actor=%s AND request_key=%s AND status='complete'", (slug,actor,request_key)).fetchone()
            if previous:
                if previous['input_hash'] != fingerprint:
                    raise ActionError('REQUEST_KEY_REUSED', 'This request key belongs to different action inputs or version', 409)
                return {**previous['result'], 'replayed': True}
            if external_guard:
                with factory() as audit:
                    attempt = audit.execute('SELECT input_hash FROM rvbbit.app_action_external_attempts WHERE slug=%s AND actor=%s AND request_key=%s',
                                            (slug, actor, request_key)).fetchone()
                if attempt:
                    if attempt['input_hash'] != fingerprint:
                        raise ActionError('REQUEST_KEY_REUSED', 'This request key belongs to another external action or inputs', 409)
                    raise ActionError('EXTERNAL_OUTCOME_UNKNOWN', 'An external call was already attempted. Inspect its outcome before starting a new operation; this request will not execute it again.', 409)
            results = []
            for step in action['steps']:
                remaining = 15000 - int((time.monotonic()-started)*1000)
                if remaining <= 0:
                    raise ActionError('ACTION_TIMEOUT', 'Action exceeded 15 seconds; rolled back', 408)
                conn.execute("SELECT set_config('statement_timeout',%s,true)", (str(remaining),))
                step_plan = workspace_sql.plan(step['sql'])
                executed_sql = workspace_sql._execution_sql(step['sql'], batch['operators'], batch.get('operator_adapters'))
                statement, indices = statement_parameters(executed_sql)
                if external_guard and step_plan['operators'] and not external_started:
                    # Persist intent outside the business transaction. A crash or
                    # rollback after dispatch must not silently repeat an API write.
                    with factory() as audit:
                        with audit.transaction():
                            claimed = audit.execute('INSERT INTO rvbbit.app_action_external_attempts(slug,actor,request_key,input_hash,run_id,app_version,action) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING run_id',
                                (slug,actor,request_key,fingerprint,run_id,version,name)).fetchone()
                    if not claimed:
                        raise ActionError('EXTERNAL_OUTCOME_UNKNOWN', 'This external operation was already attempted. Inspect its outcome before starting another.', 409)
                    external_started = True
                with psycopg.RawCursor(conn, row_factory=dict_row) as cur:
                    params = step.get('params', [])
                    cur.execute(statement, [actor_value(actor) if actor_parameter(params[i]) else values[params[i]] for i in indices])
                    if 'expect_rows' in step and cur.rowcount != step['expect_rows']:
                        raise ActionError('CONFLICT', 'The record changed or no longer exists. Refresh and try again.', 409)
                    rows = cur.fetchmany(200) if cur.description else []
                    truncated = bool(cur.fetchone()) if cur.description else False
                    results.append({'rows':rows,'rows_affected':cur.rowcount,'truncated':truncated})
            result = json.loads(json.dumps({'run_id':run_id,'committed':True,'replayed':False,
                'version':version,'action':name,'results':results,'rows':results[-1]['rows']}, default=str))
            if len(json.dumps(result)) > 2_000_000:
                raise ActionError('RESULT_TOO_LARGE', 'Return a smaller result from this action')
            record(conn, 'complete', result)
            conn.commit()  # Business writes and retry receipt commit together.
            return result
    except Exception as exc:
        if external_started:
            error = ActionError('EXTERNAL_OUTCOME_UNKNOWN', 'Local changes did not confirm completion, but the external call may have taken effect. Inspect the external result before starting a new operation; this request will not execute it again.', 409)
        elif isinstance(exc, ActionError):
            error = exc
        elif isinstance(exc, psycopg.OperationalError):
            error = ActionError('ACTION_UNCERTAIN', 'The database response was interrupted. Retry with the same request key to recover the outcome.', 503)
        else:
            error = ActionError('ACTION_FAILED', 'Action rolled back. ' + str(exc).split('\n')[0][:300], 400)
        # Failures are quiet inventory, not chat messages; never mask the original
        # outcome if the database is unavailable while recording a failure.
        try:
            with factory() as conn:
                record(conn, 'unknown' if external_started or (isinstance(exc, ActionError) and exc.code == 'EXTERNAL_OUTCOME_UNKNOWN') or isinstance(exc, psycopg.OperationalError) else 'failed', {'error':{'code':error.code,'message':str(error)}})
        except Exception:
            pass
        raise error from None


def recent(factory, slug):
    with factory() as conn:
        if not conn.execute("SELECT to_regclass('rvbbit.app_action_runs') AS tbl").fetchone()['tbl']:
            return []
        if not conn.execute("SELECT to_regclass('rvbbit.app_action_external_attempts') AS tbl").fetchone()['tbl']:
            # A previously deployed local-only app may not have run since upgrade.
            return conn.execute("SELECT id,app_version,action,actor,status,elapsed_ms,created_at, "
                                "CASE WHEN status <> 'complete' THEN result ELSE NULL END AS error "
                                "FROM rvbbit.app_action_runs WHERE slug=%s ORDER BY created_at DESC LIMIT 20", (slug,)).fetchall()
        # Claims with no final run survive process loss and remain observable.
        return conn.execute("""
            SELECT * FROM (
              SELECT id,app_version,action,actor,status,elapsed_ms,created_at,
                     CASE WHEN status <> 'complete' THEN result ELSE NULL END AS error
                FROM rvbbit.app_action_runs WHERE slug=%s
              UNION ALL
              SELECT a.run_id,a.app_version,a.action,a.actor,'unconfirmed',NULL,a.created_at,
                     '{"error":{"code":"EXTERNAL_OUTCOME_UNKNOWN","message":"External dispatch has no final receipt yet; it may be running or interrupted. Do not repeat it blindly."}}'::jsonb
                FROM rvbbit.app_action_external_attempts a WHERE a.slug=%s
                  AND NOT EXISTS(SELECT 1 FROM rvbbit.app_action_runs r WHERE r.id=a.run_id)
            ) history ORDER BY created_at DESC LIMIT 20
        """, (slug,slug)).fetchall()


def example():
    return {
        'template_html': (Path(__file__).parent / 'examples' / 'workspace-task-app.html').read_text(),
        'setup_sql': 'CREATE TABLE calliope_apps.app_tasks(id uuid PRIMARY KEY DEFAULT gen_random_uuid(), title text NOT NULL, status text NOT NULL DEFAULT \'open\' CHECK(status IN (\'open\',\'done\')), version integer NOT NULL DEFAULT 1);',
        'manifest': {'actions': {
            'add_task': {'description': 'Add a task', 'inputs': {'title': {'type':'string','max_length':200}},
                'steps':[{'sql':'INSERT INTO calliope_apps.app_tasks(title) VALUES($1) RETURNING *','params':['title'],'expect_rows':1}]},
            'set_status': {'description':'Change status if the record still matches the version the user saw',
                'inputs': {'id':{'type':'uuid'},'status':{'type':'string','enum':['open','done']},'version':{'type':'integer'}},
                'steps':[{'sql':'UPDATE calliope_apps.app_tasks SET status=$2,version=version+1 WHERE id=$1::uuid AND version=$3 RETURNING *',
                          'params':['id','status','version'],'expect_rows':1}]},
        }},
        'browser_example': "const added = await rvbbitAction('add_task', {title:'Review forecast'}); "
            "const task = added.rows[0]; await rvbbitAction('set_status', {id:task.id,status:'done',version:task.version}); "
            "const fresh = await rvbbitQuery('SELECT * FROM calliope_apps.app_tasks ORDER BY title');",
        'rules': [
            'Use calliope_apps for application state and calliope for derived data. Use an app-specific table prefix. Create/migrate tables at build time with run_workspace_sql. App rollback does not roll back data.',
            'Publish the actions with create_live_app/update_live_app manifest. Each HTML version executes its own action definitions, including old open tabs.',
            'Use bound $1 parameters, listed by input name in each step.params. Never interpolate user data into SQL.',
            'Step params can include {"actor":true} to bind the authenticated caller. Use that server value in ownership/assignment predicates; never trust an email supplied in inputs. No extra action input is required. All write steps must respect the intended access boundary.',
            'Inputs support string, integer, number, boolean, uuid, json; optional nullable, enum, max_length. All declared keys must be supplied.',
            'Each action is one atomic transaction of up to 12 SQL steps: INSERT, UPDATE, DELETE, MERGE, SELECT; local writes target calliope_apps or calliope. Registered RVBBIT/MCP SQL calls also work, including pure SELECT actions. No DDL.',
            'Operators use the existing dynamic registration/adapters, never a per-function allowlist. Set external_effects:read_only only when ALL external calls are reads. Default may_write persists dispatch intent; if completion is uncertain, EXTERNAL_OUTCOME_UNKNOWN stops the same key from repeating external effects. Inspect the provider outcome before issuing a new intentional operation. PostgreSQL rollback cannot undo external calls.',
            'Use database constraints for invariants. For concurrent edits use a version column, WHERE version=$N, increment it and expect_rows:1. CONFLICT rolls back the whole action; reload and let the user reconcile.',
            'Return values with RETURNING or a final SELECT. Result.rows is the final step; result.results contains each step, up to 200 rows per step.',
            'Disable submit while pending. On uncertain network outcomes retry the same action/inputs with {requestKey:error.requestKey}; a committed request replays without writing again. New intentional operations use new keys.',
            'rvbbitQuery defaults to uncached reads for apps with actions. Await the action then reload or apply returned rows; do not start a concurrent read before commit.',
            'Actions work in full-page apps and stage frames. Existing authenticated artifact viewers can execute them; no additional per-user or per-row policy in this slice.',
            'Actions are disabled in automated captures and historical data views. No chat/Floor notifications. debug_live_app includes recent action_runs for quiet observability.',
            'Use ordinary HTML controls, validation messages, pending states, and visible errors. Do not invoke actions during page initialization.'
        ],
    }
