"""Bounded semantic UI decisions. No caller-supplied SQL, tools or mutations.

The existing decision_tree SQL interpreter owns inference and its cache. This
adapter accepts only evidence plus inert policy JSON and returns its receipt.
"""
import asyncio
import hashlib
import hmac
import json
import os
import threading
import time
from pathlib import Path

SLOTS = threading.BoundedSemaphore(4)


def validate(body):
    if not isinstance(body, dict) or set(body) - {'state', 'tree', 'max_steps'}:
        raise ValueError('Use state, tree and optional max_steps')
    if not isinstance(body.get('state'), dict) or len(json.dumps(body['state'], allow_nan=False).encode()) > 24000:
        raise ValueError('Evidence must be an object of at most 24 KB')
    steps = body.get('max_steps', 4)
    if type(steps) is not int or not 1 <= steps <= 4:
        raise ValueError('UI decisions allow 1..4 steps')
    tree = body.get('tree')
    if not isinstance(tree, dict) or len(json.dumps(tree, allow_nan=False).encode()) > 32000:
        raise ValueError('Policy must be an object of at most 32 KB')
    # Raw JSON gets the same default as rvbbit.branch(...). The SQL interpreter
    # requires an explicit numeric threshold, unlike the constructor's API.
    tree = json.loads(json.dumps(tree, allow_nan=False))
    count = 0
    def walk(node, depth=0):
        nonlocal count
        count += 1
        if count > 63 or depth > 4 or not isinstance(node, dict):
            raise ValueError('Policy exceeds 63 nodes or four decisions')
        kind = node.get('kind')
        if kind == 'leaf':
            if set(node) != {'kind', 'label'} or not isinstance(node['label'], str) or not node['label'].strip() or not 1 <= len(node['label']) <= 100:
                raise ValueError('Invalid leaf')
            return
        if not isinstance(node.get('question'), str) or not node['question'].strip() or not 1 <= len(node['question']) <= 1800:
            raise ValueError('A bounded question is required')
        if 'inputs' in node:
            inputs = node['inputs']
            if not isinstance(inputs, list) or not 1 <= len(inputs) <= 16 or not all(isinstance(k, str) and k.strip() and len(k) <= 80 for k in inputs) or len(set(inputs)) != len(inputs):
                raise ValueError('Inputs must be 1..16 distinct top-level field names')
        if kind == 'branch':
            node.setdefault('threshold', .5)
            if set(node) - {'kind','question','yes','no','threshold','inputs'} or type(node.get('threshold', .5)) not in (float,int) or not 0 <= node.get('threshold', .5) <= 1:
                raise ValueError('Invalid binary branch')
            children = [node.get('yes'), node.get('no')]
        elif kind == 'choice':
            choices, routes = node.get('choices'), node.get('routes')
            if set(node) - {'kind','question','choices','routes','inputs'} or not isinstance(choices,dict) or not 2 <= len(choices) <= 8 or not all(isinstance(k,str) and k.strip() and len(k) <= 80 and isinstance(v,str) and v.strip() and 1 <= len(v) <= 1000 for k,v in choices.items()) or not isinstance(routes,dict) or set(choices) != set(routes):
                raise ValueError('Use 2..8 named choices with matching routes')
            children = routes.values()
        else:
            raise ValueError('Unknown policy node')
        for child in children:
            walk(child, depth+1)
    walk(tree)
    return body['state'], tree, steps


def evaluate(conn_factory, body, *, capture_usage=False):
    state, tree, steps = validate(body)
    if not SLOTS.acquire(blocking=False):
        raise RuntimeError('Decision service is busy; try again')
    started = time.monotonic()
    try:
        with conn_factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '8000ms'")
                if capture_usage:
                    conn.execute("SET LOCAL rvbbit.decision_usage='on'")
                row = conn.execute('SELECT rvbbit.decision_tree(%s::jsonb,%s::jsonb,%s) AS decision',
                                   (json.dumps(state), json.dumps(tree), steps)).fetchone()
        return {'decision': row['decision'], 'elapsed_ms': round((time.monotonic()-started)*1000)}
    finally:
        SLOTS.release()


def register(mcp, conn_factory):
    from starlette.responses import JSONResponse
    import calliope

    @mcp.custom_route('/api/calliope/ui/decision', methods=['POST'])
    async def decision(request):
        # The optional Studio credential authorizes only this bounded endpoint.
        digest = os.environ.get('CALLIOPE_UI_DECISION_TOKEN_SHA256', '')
        digest_file = os.environ.get('CALLIOPE_UI_DECISION_TOKEN_SHA256_FILE', '/app/ui-decision-token.sha256')
        if digest_file:
            try: digest = Path(digest_file).read_text().strip()
            except OSError: digest = ''
        token = request.headers.get('authorization', '').removeprefix('Bearer ')
        studio = len(digest) == 64 and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), digest)
        owner, session = calliope._canonical_owner(request)
        if not studio and (not owner or not session.get('mapped', True)):
            return JSONResponse({'error':'Sign in to Calliope'}, 401)
        # This is an application-host endpoint. Browsers use their local host,
        # which prevents cross-site requests from spending inference budget.
        if request.headers.get('origin'):
            return JSONResponse({'error':'Use the application host'}, 403)
        try:
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 60000:
                    return JSONResponse({'error':'Decision request exceeds 60 KB'}, 413)
            body = json.loads(raw)
            validate(body)
            result = await asyncio.to_thread(evaluate, conn_factory, body)
            return JSONResponse(result, headers={'Cache-Control':'no-store'})
        except (ValueError, TypeError, RecursionError):
            return JSONResponse({'error':'Invalid UI decision policy or evidence'}, 400)
        except Exception:
            # Provider/SQL errors can contain evidence or deployment details.
            return JSONResponse({'error':'Decision unavailable; previous view retained'}, 503)
