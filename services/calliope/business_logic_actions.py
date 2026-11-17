"""Optional terminal actions. Decisions stay in SQL; existing runners do the work.

One action on the reached leaf, one explicit execution request, one linked
receipt. No graph scheduler, automatic retries, or cross-service transaction.
"""
import copy
import hashlib
import json
import time
import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

import app_actions
import ui_decisions


def action_definition(value):
    from business_logic import bounded_text
    if not isinstance(value, dict):
        raise ValueError('An outcome action must be an object')
    kind = value.get('kind')
    label = bounded_text(value.get('label', ''), 'Action label', 100, True)
    if kind == 'sql' and not set(value) - {'kind', 'label', 'sql', 'external_effects'}:
        result = {'kind': kind, 'label': label,
                  'sql': bounded_text(value.get('sql'), 'Action SQL', 16000),
                  'external_effects': value.get('external_effects', 'may_write')}
        app_actions.normalize(manifest(result))
        return result
    if kind == 'callie' and not set(value) - {'kind', 'label', 'instruction'}:
        return {'kind': kind, 'label': label,
                'instruction': bounded_text(value.get('instruction'), 'Callie instruction', 8000)}
    raise ValueError('Outcome actions use kind sql or callie')


def manifest(action):
    return {'terminal': {'description': action.get('label', ''),
            'inputs': {'packet': {'type': 'json'}},
            'steps': [{'sql': action['sql'], 'params': ['packet']}],
            'external_effects': action.get('external_effects', 'may_write')}}


def trees(tree):
    """Return validated authoring and inference trees, keeping actions out of Jev."""
    if not isinstance(tree, dict) or len(json.dumps(tree, allow_nan=False).encode()) > 128000:
        raise ValueError('Decision and actions must fit within 128 KB')
    pure = copy.deepcopy(tree)
    actions = {}
    def strip(node, path='root', depth=0):
        if depth > 4 or not isinstance(node, dict):
            raise ValueError('Use at most four questions along a path')
        if 'action' in node:
            if node.get('kind') != 'leaf':
                raise ValueError('Attach actions to outcomes at the end of a path')
            actions[path] = action_definition(node.pop('action'))
        for key, child in children(node):
            strip(child, path + '/' + pointer(key), depth + 1)
    strip(pure)
    _, pure, _ = ui_decisions.validate({'state': {}, 'tree': pure})
    authored = copy.deepcopy(pure)
    def attach(node, path='root'):
        if path in actions:
            node['action'] = actions[path]
        for key, child in children(node):
            attach(child, path + '/' + pointer(key))
    attach(authored)
    return authored, pure


def children(node):
    if node.get('kind') == 'branch':
        return [('yes', node.get('yes')), ('no', node.get('no'))]
    if node.get('kind') == 'choice' and isinstance(node.get('routes'), dict):
        return node['routes'].items()
    return []


def pointer(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def selected_action(tree, decision):
    if decision.get('status') != 'complete':
        return None, None
    node, path = tree, 'root'
    for step in decision.get('path', []):
        if step.get('node') != path:
            raise ValueError('Decision trace does not match the saved tree')
        route = step['route']
        node = dict(children(node)).get(route)
        if not node:
            raise ValueError('Decision route does not match the saved tree')
        path += '/' + pointer(route)
    if node.get('kind') != 'leaf' or node.get('label') != decision.get('label'):
        raise ValueError('Decision did not reach its saved outcome')
    return path, node.get('action')


def inventory(tree):
    result = []
    def walk(node, path='root'):
        if node.get('action'):
            result.append({'node': path, 'outcome': node['label'], **node['action']})
        for key, child in children(node):
            walk(child, path + '/' + pointer(key))
    walk(tree)
    return result


def sql_action(factory, actor, snap, action, packet, key, *, seed, schema_allowed):
    return app_actions.execute(factory, slug='business-logic:' + snap['id'],
        version=snap['version'], name='terminal', actor=actor, request_key=key,
        manifest={'actions': manifest(action)}, inputs={'packet': packet},
        seed=seed, schema_allowed=schema_allowed)


def callie_action(factory, actor, snap, action, packet, key):
    import calliope
    # The existing mailbox owns turn delivery and recovery. Its ID is also the
    # execution request ID, so a repeated request finds the same handoff.
    with factory() as conn:
        prior = conn.execute('SELECT to_session_id FROM rvbbit.calliope_agent_messages WHERE id=%s AND owner_email=%s', (key, actor)).fetchone()
    if prior:
        sid = str(prior['to_session_id'])
    else:
        title = (action.get('label') or packet['decision']['label'])[:100]
        session = calliope._create_session_record_sync(calliope.CalliopeConfig.from_env(), factory, actor, 'Business logic · ' + title)
        sid = str(session['id'])
        prompt = action['instruction'] + '\n\nRecorded business-logic input and decision:\n' + json.dumps(packet, ensure_ascii=False)
        with factory() as conn:
            conn.execute('''INSERT INTO rvbbit.calliope_agent_messages
              (id,owner_email,kind,from_kind,from_ref,from_label,to_session_id,subject,body,payload)
              VALUES(%s,%s,'note','system',%s,'Business logic',%s,%s,%s,%s)''',
              (key, actor, snap['key'] or snap['id'], sid, title, prompt,
               Jsonb({'business_logic_execute': True, 'run_id': key, 'rule_id': snap['id'], 'version': snap['version']})))
    return {'status': 'queued', 'session_id': sid, 'message_id': key,
            'url': '/calliope?session=' + sid, 'receipt_url': '/meaning/logic?run=' + key}


def execute(factory, actor, body, *, seed, schema_allowed, dispatch_sql=None, dispatch_callie=None):
    import business_logic as logic
    import business_logic_runtime as runtime
    logic.identity(actor)
    if not isinstance(body, dict) or set(body) - {'id', 'rule', 'version', 'context', 'request_key'}:
        raise ValueError('Use id or rule, version, context and request_key')
    if bool(body.get('id')) == bool(body.get('rule')):
        raise ValueError('Choose a saved decision id or published rule key')
    key = logic.uid(body.get('request_key'))
    context = body.get('context')
    if not isinstance(context, dict) or len(json.dumps(context, allow_nan=False).encode()) > 24000:
        raise ValueError('Context must be a JSON object of at most 24 KB')
    if body.get('id'):
        if type(body.get('version')) is not int or body['version'] < 1:
            raise ValueError('Execute an exact saved version')
        record = logic.read(factory, actor, body['id'], body['version'])
        snap = {'id': record['id'], 'key': record['rule_key'] or '', 'name': record['definition']['name'],
                'version': record['version'], 'definition': record['definition'],
                'definition_hash': hashlib.sha256(json.dumps(record['definition'], sort_keys=True).encode()).hexdigest(),
                'url': record['url'] + '&version=' + str(record['version'])}
    else:
        snap = runtime.snapshot(runtime.resolve(factory, actor, body['rule'], body.get('version')))
    fingerprint = hashlib.sha256(json.dumps([snap['id'], snap['version'], context], sort_keys=True).encode()).hexdigest()
    started = time.monotonic()
    with factory() as lock:
        if not lock.execute('SELECT pg_try_advisory_lock(hashtextextended(%s,0)) AS ok', ('logic-execute:' + key,)).fetchone()['ok']:
            raise app_actions.ActionError('RUNNING', 'This execution is already running', 409)
        try:
            prior = lock.execute('SELECT owner_email,receipt FROM rvbbit.calliope_business_logic_runs WHERE id=%s', (key,)).fetchone()
            if prior:
                receipt = prior['receipt']
                if prior['owner_email'] != actor or receipt.get('fingerprint') != fingerprint:
                    raise app_actions.ActionError('REQUEST_KEY_REUSED', 'This request key belongs to another execution', 409)
                # Reopening/retrying a finished request never re-evaluates it.
                if receipt['status'] != 'running':
                    return response(receipt, replayed=True)
            else:
                receipt = {'id': key, 'type': 'business_logic', 'source': 'execute', 'status': 'running',
                    'fingerprint': fingerprint, 'created_at': datetime.now(timezone.utc).isoformat(),
                    'elapsed_ms': 0, 'evaluations': 0, 'outcomes': {}, 'statuses': {}, 'rows': [],
                    'definitions': {f"{snap['key']}@{snap['version']}": snap}, 'details_truncated': False,
                    'error': None, 'url': '/meaning/logic?run=' + key}
                lock.execute('INSERT INTO rvbbit.calliope_business_logic_runs(id,owner_email,receipt) VALUES(%s,%s,%s)', (key, actor, Jsonb(receipt)))
            def persist():
                receipt['elapsed_ms'] = round((time.monotonic() - started) * 1000)
                lock.execute('UPDATE rvbbit.calliope_business_logic_runs SET receipt=%s WHERE id=%s AND owner_email=%s', (Jsonb(receipt), key, actor))
            try:
                if not receipt['rows']:
                    _, tree = trees(snap['definition']['tree'])
                    # Use the named evaluator's field contract without requiring
                    # publication for an owner's explicit saved-draft experiment.
                    errors = runtime.input_errors(snap['definition'], context)
                    decision = ({'status': 'invalid_input', 'label': None, 'path': [], 'input_errors': errors} if errors
                                else ui_decisions.evaluate(factory, {'state': context, 'tree': tree})['decision'])
                    decision['rule'] = {k: v for k, v in snap.items() if k != 'definition'}
                    receipt.update(evaluations=1, outcomes={decision.get('label') or '(no outcome)': 1},
                                   statuses={decision['status']: 1}, rows=[{'row': 1, 'input': context, 'decision': decision}])
                    persist()
                decision = receipt['rows'][0]['decision']
                path, action = selected_action(snap['definition']['tree'], decision)
                if decision['status'] != 'complete':
                    receipt['status'] = 'stopped'
                elif not action:
                    receipt.update(status='complete', action={'status': 'none', 'node': path})
                else:
                    packet = {'input': context, 'decision': decision}
                    receipt['action'] = {'node': path, 'kind': action['kind'], 'label': action.get('label') or decision['label'],
                                         'status': 'running', 'input': packet}
                    persist()
                    if action['kind'] == 'sql':
                        result = (dispatch_sql or sql_action)(factory, actor, snap, action, packet, key, seed=seed, schema_allowed=schema_allowed)
                    else:
                        result = (dispatch_callie or callie_action)(factory, actor, snap, action, packet, key)
                    receipt['action'].update(status=result.get('status', 'complete'), result=result)
                    receipt['status'] = 'complete'
            except Exception as exc:
                code = getattr(exc, 'code', 'ACTION_FAILED')
                receipt.update(status='unknown' if code in {'EXTERNAL_OUTCOME_UNKNOWN', 'ACTION_UNCERTAIN'} else 'failed', error=str(exc)[:600])
                if receipt.get('action'):
                    receipt['action'].update(status=receipt['status'], error=receipt['error'], code=code)
            persist()
            return response(receipt)
        finally:
            lock.execute('SELECT pg_advisory_unlock(hashtextextended(%s,0))', ('logic-execute:' + key,))


def response(receipt, replayed=False):
    import business_logic_runtime as runtime
    return {'decision': receipt['rows'][0]['decision'] if receipt['rows'] else None,
            'action': receipt.get('action'), 'receipt': runtime.receipt_summary(receipt), 'replayed': replayed}
