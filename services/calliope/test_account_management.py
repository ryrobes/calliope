"""Real Postgres account lifecycle and browser/OAuth authorization boundaries."""
import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import urlparse,parse_qs

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
from starlette.requests import Request

import auth
import account_management as accounts
import account_management_web as web
import application_teams as teams
from app_actions import ActionError
from test_business_logic import database

ADMIN='owner@example.com'
PASSWORD='an original bootstrap password'
NEXT='a different personal passphrase'

@pytest.fixture
def store(database,monkeypatch):
    monkeypatch.setenv('WAREHOUSE_TEAM_BOOTSTRAP_ADMINS',ADMIN)
    teams.ensure_tables(database)
    with database() as c:
        c.execute('ALTER TABLE rvbbit.application_principals ADD COLUMN IF NOT EXISTS login_allowed boolean NOT NULL DEFAULT false')
        c.execute(accounts.Path(accounts.__file__).with_suffix('.sql').read_text())
        c.execute('TRUNCATE rvbbit.application_account_links,rvbbit.application_accounts,rvbbit.application_login_settings,rvbbit.application_account_events,rvbbit.team_members')
        c.execute('UPDATE rvbbit.application_principals SET login_allowed=false')
        c.execute("INSERT INTO rvbbit.application_principals(email) VALUES(%s) ON CONFLICT DO NOTHING",(ADMIN,))
        c.execute('INSERT INTO rvbbit.team_members(team_id,principal_email,added_by) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(teams.ADMIN_TEAM_ID,ADMIN,'test'))
    monkeypatch.setattr(auth,'ACCOUNTS',None)
    monkeypatch.setattr(auth,'AUTH_MODE','shared')
    monkeypatch.setattr(auth,'JWT_SECRET','test-signing-secret-independent-of-any-password')
    monkeypatch.setattr(auth,'LOGIN_PASSWORD',PASSWORD)
    monkeypatch.setattr(auth,'ALLOWED_EMAILS',{ADMIN})
    monkeypatch.setattr(auth,'GOOGLE_CLIENT_ID','')
    monkeypatch.setattr(auth,'GOOGLE_CLIENT_SECRET','')
    monkeypatch.setattr(auth,'GOOGLE_HD','')
    monkeypatch.setattr(auth,'GOOGLE_ONLY',False)
    monkeypatch.setattr(auth,'STATE_FILE','')
    monkeypatch.setattr(auth,'_LIMITER',auth._RateLimiter())
    monkeypatch.setattr(auth,'_GFLOWS',auth._GoogleFlows())
    result=accounts.Accounts(database,auth.JWT_SECRET)
    result.initialize(auth)
    monkeypatch.setattr(auth,'ACCOUNTS',result)
    return result

@pytest.fixture
def client(store):
    routes=[]
    class M:
        def custom_route(self,path,methods):
            def add(fn):routes.append(Route(path,fn,methods=methods));return fn
            return add
    m=M();provider=auth.WarehouseAuthProvider('https://testserver')
    auth.register_login_route(m,provider)
    web.register(m,store.factory,provider)
    with TestClient(Starlette(routes=routes),base_url='https://testserver') as c:
        c.provider=provider
        yield c


def login(client,email=ADMIN,password=PASSWORD):
    return client.post('/login',data={'email':email,'password':password,'next':'/account'},follow_redirects=False)


def post(client,action,data,**kwargs):
    return client.post('/api/calliope/account/'+action,json=data,headers={'X-Calliope-Account':'1','Origin':'https://testserver'},**kwargs)


def test_bootstrap_migrates_once_and_new_password_overrides_environment(store,monkeypatch):
    assert store.authenticate(ADMIN,PASSWORD)
    secret=store.account(ADMIN)['password_secret']
    assert PASSWORD not in secret and 'scrypt' not in secret
    store.change_password(ADMIN,{'current_password':PASSWORD,'password':NEXT},{'via':'password'})
    monkeypatch.setattr(auth,'LOGIN_PASSWORD','')
    store.initialize(auth)
    assert store.authenticate(ADMIN,NEXT) and not store.authenticate(ADMIN,PASSWORD)
    assert auth.validate_config()==[]
    assert not store.authenticate('somebody@example.com',PASSWORD)


def test_password_change_keeps_current_browser_and_revokes_old_browser_access_and_refresh_tokens(client,store):
    assert login(client).status_code==302
    old_cookie=client.cookies.get(auth.SESSION_COOKIE)
    issued=client.provider._issue('a-client',ADMIN,[auth.SCOPE])
    assert post(client,'password',{'current_password':'wrong','password':NEXT}).status_code==400
    assert post(client,'password',{'current_password':PASSWORD,'password':NEXT}).status_code==200
    assert client.get('/api/calliope/account/me').status_code==200
    request=Request({'type':'http','headers':[(b'cookie',f'{auth.SESSION_COOKIE}={old_cookie}'.encode())]})
    assert auth.read_session_full(request) is None
    assert asyncio.run(client.provider.load_access_token(issued.access_token)) is None
    assert asyncio.run(client.provider.load_refresh_token(SimpleNamespace(client_id='a-client'),issued.refresh_token)) is None
    assert login(client,password=PASSWORD).status_code==401
    assert login(client,password=NEXT).status_code==302


def test_setup_links_are_single_use_expiring_and_do_not_log_secrets(client,store):
    login(client)
    created=post(client,'people',{'email':'new@example.com','display_name':'New Person'})
    assert created.status_code==200,created.text
    token=created.json()['setup_token']
    assert not store.authenticate('new@example.com',PASSWORD)
    replacement=post(client,'setup-link',{'email':'new@example.com'}).json()['setup_token']
    assert post(client,'setup',{'token':token,'password':NEXT}).status_code==400
    assert post(client,'setup',{'token':replacement,'password':NEXT}).status_code==200
    assert client.get('/api/calliope/account/me').json()['email']=='new@example.com'
    assert post(client,'setup',{'token':replacement,'password':NEXT}).status_code==400
    assert client.get('/api/calliope/account/people').status_code==403
    with store.factory() as c:
        events=c.execute('SELECT detail::text FROM rvbbit.application_account_events').fetchall()
        assert token not in str(events) and replacement not in str(events) and NEXT not in str(events)
    login(client)
    expired=post(client,'setup-link',{'email':'new@example.com'}).json()['setup_token']
    with store.factory() as c:c.execute("UPDATE rvbbit.application_account_links SET expires_at=now()-interval '1 second' WHERE email='new@example.com'")
    assert post(client,'setup',{'token':expired,'password':NEXT}).status_code==400


def test_disable_wins_over_allowlist_and_revokes_tokens_without_deleting_work(client,store):
    login(client)
    result=post(client,'people',{'email':'member@example.com'}).json()
    post(client,'setup',{'token':result['setup_token'],'password':NEXT})
    token=client.provider._issue('a-client','member@example.com',[auth.SCOPE])
    old=client.cookies.get(auth.SESSION_COOKIE)
    login(client)
    assert post(client,'person',{'email':'member@example.com','enabled':False}).status_code==200
    assert not store.allowed('member@example.com')
    assert asyncio.run(client.provider.load_access_token(token.access_token)) is None
    request=Request({'type':'http','headers':[(b'cookie',f'{auth.SESSION_COOKIE}={old}'.encode())]})
    assert auth.read_session_full(request) is None
    assert store.account('member@example.com') is not None
    assert post(client,'person',{'email':ADMIN,'enabled':False}).status_code==409
    assert post(client,'person',{'email':'member@example.com','enabled':True}).status_code==200
    assert store.authenticate('member@example.com',NEXT)
    assert asyncio.run(client.provider.load_access_token(token.access_token)) is None


def test_routes_reject_csrf_nonadmins_and_secret_reads(client,store):
    assert client.get('/api/calliope/account/me').status_code==401
    login(client)
    assert client.post('/api/calliope/account/people',json={'email':'bad@example.com'}).status_code==403
    assert client.post('/api/calliope/account/people',json={'email':'bad@example.com'},headers={'X-Calliope-Account':'1','Origin':'https://evil.example'}).status_code==403
    conf=client.get('/api/calliope/account/settings').json()
    assert 'google_client_secret' not in conf
    conf.update(google_client_id='client.apps.googleusercontent.com',google_client_secret='a-secret',allowed_emails=['friend@gmail.com'])
    saved=post(client,'settings',conf)
    assert saved.status_code==200,saved.text
    assert 'a-secret' not in saved.text
    assert saved.json()['has_google_secret']
    assert post(client,'settings',conf).status_code==409
    assert auth.google_enabled()
    assert store.allowed('friend@gmail.com') and not store.allowed('stranger@gmail.com')


def test_google_uses_only_identity_scopes_and_binds_callback_to_same_browser(client,store,monkeypatch):
    login(client)
    conf=client.get('/api/calliope/account/settings').json()
    conf.update(google_client_id='client.apps.googleusercontent.com',google_client_secret='a-secret',allowed_emails=['friend@gmail.com'])
    post(client,'settings',conf)
    response=client.get('/auth/google/start?next=/account',follow_redirects=False)
    args=parse_qs(urlparse(response.headers['location']).query)
    assert args['scope']==['openid email profile']
    assert args['redirect_uri']==['https://testserver/auth/google/callback']
    assert client.cookies.get('wh_google_flow')==args['state'][0]
    client.cookies.delete('wh_google_flow')
    response=client.get('/auth/google/callback?'+__import__('urllib.parse',fromlist=['urlencode']).urlencode({'state':args['state'][0],'code':'never-exchanged'}))
    assert response.status_code==400
    assert store.google_login({'email':'friend@gmail.com','sub':'stable-google-sub','name':'Friend'})=='friend@gmail.com'
    assert store.account('friend@gmail.com')['google_subject']=='stable-google-sub'
    with pytest.raises(ActionError):store.google_login({'email':'friend@gmail.com','sub':'different-google-account'})
    with pytest.raises(ActionError):store.google_login({'email':'outsider@gmail.com','sub':'another-sub'})


def test_groups_reuse_existing_memberships_and_do_not_rebootstrap_removed_admins(store):
    authz=SimpleNamespace(subject=ADMIN,actor=ADMIN,mode='browser_session')
    result=store.create(ADMIN,{'email':'second@example.com'})
    store.redeem(result['setup_token'],NEXT)
    group=teams.create_team(store.factory,authz,'Dispatch','Dispatch access',['second@example.com'])['team']
    assert group['members']==['second@example.com']
    team=teams.get_team(store.factory,authz,teams.ADMIN_TEAM_ID)['team']
    teams.update_team(store.factory,authz,team['id'],team['revision'],add_members=['second@example.com'])
    team=teams.get_team(store.factory,authz,teams.ADMIN_TEAM_ID)['team']
    teams.update_team(store.factory,authz,team['id'],team['revision'],remove_members=[ADMIN])
    teams.ensure_tables(store.factory)
    with store.factory() as c:assert not teams._is_admin(c,ADMIN)
    # A disabled remaining member cannot be used to remove the last active admin.
    store.update('second@example.com',ADMIN,{'enabled':False})
    team=teams.get_team(store.factory,SimpleNamespace(subject='second@example.com'),teams.ADMIN_TEAM_ID)['team']
    with pytest.raises(Exception):teams.update_team(store.factory,SimpleNamespace(subject='second@example.com'),team['id'],team['revision'],remove_members=['second@example.com'])


def test_profile_edit_does_not_turn_old_google_login_into_recent_verification(client,store):
    with store.factory() as c:c.execute('UPDATE rvbbit.application_accounts SET password_secret=NULL WHERE email=%s',(ADMIN,))
    from starlette.responses import Response
    response=Response();auth.set_session(response,ADMIN,True,via='google',auth_time=int(time.time())-3600)
    cookie=response.headers['set-cookie'].split(';')[0].split('=',1)[1]
    client.cookies.set(auth.SESSION_COOKIE,cookie)
    assert post(client,'me',{'display_name':'New name'}).status_code==200
    assert post(client,'password',{'password':NEXT}).status_code==403


def test_google_callback_verifies_id_token_links_allowed_account_and_denies_others(client,store,monkeypatch):
    import httpx,jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    monkeypatch.setattr(auth,'_JWKS_CLIENT',SimpleNamespace(get_signing_key_from_jwt=lambda _:SimpleNamespace(key=key.public_key())))
    login(client)
    conf=client.get('/api/calliope/account/settings').json()
    conf.update(google_client_id='client.apps.googleusercontent.com',google_client_secret='a-secret',allowed_emails=['friend@gmail.com'])
    post(client,'settings',conf)
    claims={}
    class Provider:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,data):
            assert url==auth._GOOGLE_TOKEN_URI and data['client_secret']=='a-secret'
            return httpx.Response(200,json={'id_token':jwt.encode(claims,key,algorithm='RS256')},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx,'AsyncClient',Provider)
    for person,verified,expected in [('friend@gmail.com',True,302),('stranger@gmail.com',True,401),('friend@gmail.com',False,401)]:
        response=client.get('/auth/google/start?next=/account',follow_redirects=False)
        args=parse_qs(urlparse(response.headers['location']).query)
        claims.update(iss='https://accounts.google.com',aud='client.apps.googleusercontent.com',sub='google:'+person,
                      email=person,email_verified=verified,nonce=args['nonce'][0],iat=int(time.time()),exp=int(time.time())+300)
        callback='/auth/google/callback?state='+args['state'][0]+'&code=exchanged-once'
        response=client.get(callback,follow_redirects=False)
        assert response.status_code==expected,response.text
        assert client.get(callback,follow_redirects=False).status_code==400
    assert store.account('friend@gmail.com')['google_subject']=='google:friend@gmail.com'
    assert store.account('stranger@gmail.com') is None


def test_pages_use_shared_chrome_and_setup_page_does_not_require_existing_session(client,store):
    assert client.get('/account',follow_redirects=False).status_code==302
    page=client.get('/account/setup')
    assert page.status_code==200 and 'Choose your password' in page.text
    assert '/theme/workspace-page.css' in page.text and '/meaning/meaning.css' not in page.text
    assert 'system-workspace-open' not in page.text
    login(client)
    page=client.get('/account')
    assert page.status_code==200 and 'People &amp; access' in page.text and 'My account' in page.text
    assert 'no-store' in page.headers['cache-control']
    assert 'system-workspace-open' in page.text
    assert '__CALLIOPE_HEADER__' not in page.text
    assert client.get('/account/assets/accounts.js').status_code==200
    assert client.get('/account/assets/auth.py').status_code==404


def test_google_only_requires_working_admin_link_and_closes_password_and_setup_paths(client,store):
    login(client)
    conf=client.get('/api/calliope/account/settings').json()
    conf.update(google_client_id='client.apps.googleusercontent.com',google_client_secret='a-secret',google_only=True)
    assert post(client,'settings',conf).status_code==400
    store.google_login({'email':ADMIN,'sub':'owner-google'})
    assert post(client,'settings',conf).status_code==200
    assert not store.authenticate(ADMIN,PASSWORD)
    assert login(client).status_code==401
    with pytest.raises(ActionError) as exc:store.redeem('a'*43,NEXT)
    assert exc.value.code=='GOOGLE_ONLY'


def test_last_active_admin_cannot_be_removed_while_only_disabled_admins_remain(store):
    authz=SimpleNamespace(subject=ADMIN,actor=ADMIN,mode='browser_session')
    created=store.create(ADMIN,{'email':'disabled-admin@example.com'})
    store.redeem(created['setup_token'],NEXT)
    group=teams.get_team(store.factory,authz,teams.ADMIN_TEAM_ID)['team']
    teams.update_team(store.factory,authz,group['id'],group['revision'],add_members=['disabled-admin@example.com'])
    store.update(ADMIN,'disabled-admin@example.com',{'enabled':False})
    group=teams.get_team(store.factory,authz,teams.ADMIN_TEAM_ID)['team']
    with pytest.raises(Exception,match='active administrator'):
        teams.update_team(store.factory,authz,group['id'],group['revision'],remove_members=[ADMIN])


def test_first_setup_requires_credentials_but_initialized_store_does_not(store,monkeypatch):
    monkeypatch.setattr(auth,'LOGIN_PASSWORD','')
    store.initialize(auth)
    with store.factory() as c:
        c.execute('DELETE FROM rvbbit.application_login_settings')
    with pytest.raises(ValueError,match='First-time account setup'):
        store.initialize(auth)
    with store.factory() as c:
        assert c.execute('SELECT count(*) AS n FROM rvbbit.application_login_settings').fetchone()['n']==0
