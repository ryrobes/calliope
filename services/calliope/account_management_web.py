"""First-party account settings and administrator people/access pages."""
import asyncio
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from types import SimpleNamespace

from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.encoders import jsonable_encoder

import auth
import application_teams as teams
import warehouse_theme
from app_actions import ActionError

ASSETS = Path(__file__).parent / 'calliope'
HEADERS = {'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer'}


def register(mcp, factory, provider):
    if auth.ACCOUNTS is None:
        return
    store = auth.ACCOUNTS

    def session(request):
        value = auth.read_session_full(request)
        if not value or value.get('via') not in ('password','google'):
            raise ActionError('SIGN_IN', 'Sign in to manage your account.', 401)
        return value

    def administrator(actor):
        with factory() as c:
            store.admin(c, actor)

    def authorization(actor):
        return SimpleNamespace(subject=actor,actor=actor,mode='browser_session',delegated=False,platform='web')

    def reply(value, code=200):
        return JSONResponse(jsonable_encoder(value),status_code=code,headers=HEADERS)

    async def body(request):
        if (request.headers.get('sec-fetch-site') == 'cross-site'
            or request.headers.get('origin','') not in ('',provider.public,str(request.base_url).rstrip('/'))
            or request.headers.get('content-type','').split(';')[0] != 'application/json'
            or request.headers.get('x-calliope-account') != '1'):
            raise ActionError('ORIGIN', 'Use the Calliope account settings page.', 403)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw)>20000:
                raise ActionError('TOO_LARGE','Request is too large.',413)
        value = json.loads(raw)
        if not isinstance(value,dict):
            raise ValueError('Expected an object.')
        return value

    async def page(request):
        setup = request.url.path == '/account/setup'
        try:
            viewer = None if setup else session(request)
        except ActionError:
            return RedirectResponse('/login?'+urlencode({'next':request.url.path+('?' + request.url.query if request.url.query else '')}),302)
        profile = await asyncio.to_thread(store.profile,viewer['identity']) if viewer else None
        html = (ASSETS/'accounts.html').read_text()
        version = hashlib.sha256(b''.join((ASSETS/n).read_bytes() for n in ('accounts.html','accounts.css','accounts.js'))).hexdigest()[:16]
        html = html.replace('__ACCOUNT_VERSION__',version).replace('__ACCOUNT_MODE__','setup' if setup else 'account')
        can_administer = bool(profile and profile['can_administer'])
        html = html.replace('__CALLIOPE_HEADER__', warehouse_theme.workspace_header(viewer, can_administer=can_administer))
        html = html.replace('__CALLIOPE_BACKGROUND__',auth.background_layer(.74,"linear-gradient(to bottom,rgba(16,13,11,.10),rgba(16,13,11,.40) 86%)",scene_key=viewer['identity'] if viewer else 'account'))
        return HTMLResponse(html,headers={**HEADERS,'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})

    mcp.custom_route('/account',methods=['GET'])(page)
    mcp.custom_route('/account/setup',methods=['GET'])(page)

    @mcp.custom_route('/account/assets/{asset}',methods=['GET'])
    async def asset(request):
        name=request.path_params['asset']
        if name not in ('accounts.css','accounts.js'):
            return Response(status_code=404)
        return Response((ASSETS/name).read_bytes(),media_type='text/css' if name.endswith('.css') else 'text/javascript',headers=HEADERS)

    @mcp.custom_route('/api/calliope/account/{action}',methods=['GET','POST'])
    async def api(request):
        action=request.path_params['action']
        try:
            data=await body(request) if request.method=='POST' else {}
            if action=='setup' and request.method=='POST':
                key='account-setup:'+auth._client_ip(request)
                async with auth._LIMITER.lock:
                    if auth._LIMITER.blocked(key):
                        raise ActionError('RATE_LIMIT','Too many attempts. Try again in a few minutes.',429)
                    try:
                        actor=await asyncio.to_thread(store.redeem,data.get('token'),data.get('password'))
                    except ActionError:
                        auth._LIMITER.record_fail(key)
                        raise
                    auth._LIMITER.record_success(key)
                result=reply({'ok':True})
                auth.set_session(result,actor,provider.public.startswith('https://'))
                return result
            viewer=session(request)
            actor=viewer['identity']
            post=request.method=='POST'
            if action=='me':
                if post:
                    await asyncio.to_thread(store.save_profile,actor,data)
                result=await asyncio.to_thread(store.profile,actor)
                result['via']=viewer['via']
                result['google_enabled']=auth.google_enabled()
                result['google_only']=auth._google_only()
                response=reply(result)
                if post:
                    auth.set_session(response,viewer['sub'],provider.public.startswith('https://'),identity=actor,via=viewer['via'],name=result['display_name'] or '',picture=viewer.get('picture',''),auth_time=viewer.get('auth_time',0),account_version=viewer.get('account_version',0))
                return response
            if action=='password' and post:
                key='account-password:'+actor
                async with auth._LIMITER.lock:
                    if auth._LIMITER.blocked(key):
                        raise ActionError('RATE_LIMIT','Too many attempts. Try again in a few minutes.',429)
                    try:
                        result=await asyncio.to_thread(store.change_password,actor,data,viewer)
                    except ActionError:
                        auth._LIMITER.record_fail(key)
                        raise
                    auth._LIMITER.record_success(key)
                response=reply(result)
                auth.set_session(response,viewer['sub'],provider.public.startswith('https://'),identity=actor,via=viewer['via'],name=viewer.get('name',''),picture=viewer.get('picture',''))
                return response
            await asyncio.to_thread(administrator,actor)
            if action=='people':
                result=await asyncio.to_thread(store.create if post else store.people,actor,*([data] if post else []))
            elif action=='person' and post:
                result=await asyncio.to_thread(store.update,actor,data.get('email'),data)
            elif action=='setup-link' and post:
                result=await asyncio.to_thread(store.setup_link,actor,data.get('email'))
            elif action=='settings':
                result=await asyncio.to_thread(store.save_settings if post else store.settings,actor,*([data] if post else []))
                result['redirect_uri']=auth.google_redirect_uri(provider.public)
                result['google_enabled']=auth.google_enabled()
            elif action=='groups':
                if post:
                    result=await asyncio.to_thread(teams.create_team,factory,authorization(actor),data.get('name'),data.get('description',''),data.get('members',[]))
                else:
                    result=await asyncio.to_thread(teams.list_teams,factory,authorization(actor))
            elif action=='group' and post:
                result=await asyncio.to_thread(teams.update_team,factory,authorization(actor),data.get('id'),data.get('revision'),
                    name=data.get('name'),description=data.get('description'),add_members=data.get('add_members'),remove_members=data.get('remove_members'),archived=data.get('archived'))
            elif action=='events' and not post:
                result=await asyncio.to_thread(store.events,actor)
            else:
                return reply({'error':{'message':'Not found.'}},404)
            return reply(result)
        except (ActionError,teams.TeamError) as exc:
            return reply({'error':{'code':exc.code,'message':str(exc)}},exc.status)
        except (ValueError,TypeError) as exc:
            return reply({'error':{'message':str(exc)}},400)
        except Exception as exc:
            if 'Keep at least one active administrator' in str(exc):
                return reply({'error':{'code':'LAST_ADMIN','message':'Keep at least one active administrator.'}},409)
            # Never return credential values, token exchange errors, or DB arguments.
            return reply({'error':{'message':'Account service unavailable. Try again shortly.'}},503)
