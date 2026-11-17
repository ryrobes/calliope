"""Small web workbench for saved decision trees, with shared Calliope chrome."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlencode

from app_actions import ActionError
import business_logic as logic

ASSETS = Path(__file__).parent / 'calliope'
STATIC = {'business-logic.js': 'text/javascript', 'business-logic-model.mjs': 'text/javascript',
          'business-logic-actions.mjs': 'text/javascript',
          'business-logic-usage.mjs': 'text/javascript',
          'business-logic-capability.mjs': 'text/javascript',
          'business-logic-workspace.mjs': 'text/javascript', 'business-logic-code-editor.mjs': 'text/javascript',
          'business-logic.css': 'text/css'}


def register(mcp, factory, session_actor, server=None):
    import auth
    import warehouse_theme
    import calliope_dreams
    from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
    logic.ensure(factory)

    @mcp.custom_route('/meaning/logic', methods=['GET'])
    async def page(request):
        actor, session, error = session_actor(request)
        if error:
            return RedirectResponse('/login?' + urlencode({'next': '/meaning/logic'}), 302)
        version = hashlib.sha256(b''.join((ASSETS / p).read_bytes() for p in ['business-logic.html', *STATIC])).hexdigest()[:16]
        html = (ASSETS / 'business-logic.html').read_text().replace('__LOGIC_VERSION__', version)
        html = html.replace('__LOGIC_ROW_LIMIT__', str(logic.sql_row_limit(server)))
        html = html.replace('__LOGIC_DEFAULT_ROWS__', str(min(100, logic.sql_row_limit(server))))
        can_administer = await asyncio.to_thread(calliope_dreams.is_company_admin, factory, actor)
        html = html.replace('__CALLIOPE_WORKSPACE_NAV__', warehouse_theme.workspace_navigation('meaning', can_administer=can_administer))
        html = html.replace('__CALLIOPE_ACCOUNT__', warehouse_theme.account_control(session, workspace_menu=True, can_administer=can_administer))
        html = html.replace('__CALLIOPE_BACKGROUND__', auth.background_layer(
            0.74,
            "radial-gradient(1000px 700px at 58% -15%, rgba(32,67,64,.10), transparent 67%),"
            "linear-gradient(to bottom,rgba(16,13,11,.10),rgba(16,13,11,.40) 86%)",
            scene_key=actor,
        ))
        return HTMLResponse(html, headers={'Cache-Control': 'private, no-store', 'Vary': 'Cookie, Authorization',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'; form-action 'self'"})

    @mcp.custom_route('/meaning/logic/{asset}', methods=['GET'])
    async def asset(request):
        _, _, error = session_actor(request)
        if error:
            return error
        name = request.path_params['asset']
        if name not in STATIC:
            return Response(status_code=404)
        return Response((ASSETS / name).read_bytes(), media_type=STATIC[name],
                        headers={'Cache-Control': 'private, no-cache', 'X-Content-Type-Options': 'nosniff'})

    async def dispatch(request, action):
        actor, _, error = session_actor(request)
        if error:
            return error
        try:
            body = {}
            if request.method == 'POST':
                allowed = {str(request.base_url).rstrip('/'), os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')}
                if (request.headers.get('sec-fetch-site') == 'cross-site'
                        or (request.headers.get('origin') and request.headers['origin'] not in allowed)
                        or request.headers.get('content-type', '').split(';')[0] != 'application/json'):
                    return JSONResponse({'error': {'message': 'Use the Calliope workbench'}}, 403)
                raw = bytearray()
                async for chunk in request.stream():
                    raw.extend(chunk)
                    if len(raw) > 180000:
                        return JSONResponse({'error': {'message': 'Request exceeds 180 KB'}}, 413)
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError('Expected a JSON object')
            result = await asyncio.to_thread(action, actor, body)
            return JSONResponse(result, headers={'Cache-Control': 'private, no-store'})
        except ActionError as exc:
            return JSONResponse({'error': {'code': exc.code, 'message': str(exc)}}, exc.status)
        except (ValueError, TypeError, RecursionError) as exc:
            return JSONResponse({'error': {'message': str(exc) if not isinstance(exc, RecursionError) else 'Tree is too deep'}}, 400)
        except Exception:
            return JSONResponse({'error': {'message': 'Decision service unavailable. Your edits are still here; retry shortly.'}}, 503)

    @mcp.custom_route('/api/calliope/business-logic', methods=['GET', 'POST'])
    async def collection(request):
        return await dispatch(request, lambda actor, body: logic.save(factory, actor, body) if request.method == 'POST'
                              else logic.catalog(factory, actor, request.query_params.get('q', '')))

    @mcp.custom_route('/api/calliope/business-logic/test', methods=['POST'])
    async def test(request):
        return await dispatch(request, lambda actor, body: logic.evaluate(factory, actor, body))

    @mcp.custom_route('/api/calliope/business-logic/sql', methods=['POST'])
    async def sql_rows(request):
        return await dispatch(request, lambda actor, body: logic.query_rows(server, actor, body))

    @mcp.custom_route('/api/calliope/business-logic/publish', methods=['POST'])
    async def publish(request):
        return await dispatch(request, lambda actor, body: logic.runtime.publish(factory,actor,body.get('id'),body.get('version')))

    @mcp.custom_route('/api/calliope/business-logic/run', methods=['POST'])
    async def run(request):
        return await dispatch(request, lambda actor, body: logic.runtime.run(factory,actor,body.get('rule'),body.get('context'),body.get('version')))

    @mcp.custom_route('/api/calliope/business-logic/execute', methods=['POST'])
    async def execute(request):
        return await dispatch(request, lambda actor, body: server._execute_business_logic_request(actor, body))

    @mcp.custom_route('/api/calliope/business-logic/receipts/{run_id}', methods=['GET'])
    async def receipt(request):
        return await dispatch(request, lambda actor, _: logic.runtime.read_receipt(factory,actor,request.path_params['run_id']))

    @mcp.custom_route('/api/calliope/business-logic/usage/{run_id}', methods=['GET'])
    async def usage(request):
        return await dispatch(request, lambda actor, _: logic.usage.read(factory,actor,request.path_params['run_id']))

    @mcp.custom_route('/api/calliope/business-logic/{logic_id}', methods=['GET'])
    async def item(request):
        return await dispatch(request, lambda actor, _: logic.read(factory, actor, request.path_params['logic_id'],
                              int(request.query_params['version']) if 'version' in request.query_params else None))
