"""Workspace inheritance and admin authorization against a disposable database."""
import ast
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import psycopg
from psycopg.rows import dict_row
import pytest
from starlette.requests import Request
from starlette.responses import JSONResponse
import calliope


@pytest.fixture
def api():
    dsn = os.environ.get('CALLIOPE_APPEARANCE_TEST_DSN')
    if not dsn:
        pytest.skip('Set CALLIOPE_APPEARANCE_TEST_DSN to an empty disposable database')
    def factory():
        return psycopg.connect(dsn, row_factory=dict_row)
    with factory() as c:
        assert c.execute("SELECT to_regnamespace('rvbbit') AS n").fetchone()['n'] is None
        c.execute('CREATE SCHEMA rvbbit')
        c.execute(calliope._APPEARANCE_DDL)
    tree = ast.parse(Path(calliope.__file__).read_text())
    names = ['hermes_admin_access', 'workspace_admin_settings', 'get_calliope_appearance', 'put_calliope_appearance']
    namespace = dict(vars(calliope), conn_factory=factory, json_response=lambda value,status=200: JSONResponse(value,status_code=status),
                     api_owner=lambda request: (request.headers['x-test-user'], None),
                     calliope_dreams=SimpleNamespace(is_company_admin=lambda _,owner: owner=='admin@example.com'))
    for name in names:
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name==name)
        node.decorator_list=[]
        exec(compile(ast.Module(body=[node],type_ignores=[]),calliope.__file__,'exec'),namespace)
    def request(route, method='GET', user='admin@example.com', body=None, headers=None):
        raw=json.dumps(body).encode() if body is not None else b''
        async def receive():return {'type':'http.request','body':raw,'more_body':False}
        h={'x-test-user':user,**(headers or {})}
        req=Request({'type':'http','method':method,'path':'/','headers':[(k.encode(),v.encode()) for k,v in h.items()]},receive)
        response=asyncio.run(namespace[route](req))
        return response.status_code,json.loads(response.body)
    try:yield request,factory
    finally:
        with factory() as c:c.execute('DROP SCHEMA rvbbit CASCADE')


def appearance():
    return {'version':1,'source':{'kind':'library','id':'callie-bg-dark','url':'/theme/images/full/callie-bg-dark.webp'},
            'palette':{'baseHue':25,'chroma':0.1,'vibrant':'#cc5500','darkVibrant':'#442200','lightVibrant':'#eeaa55','muted':'#886644','darkMuted':'#332211','lightMuted':'#ddbb99'},
            'tokens':{'--main':'#cc5500','--background':'#110900'}}


def test_inheritance_personal_override_mode_only_and_reset(api):
    request,factory=api
    status,body=request('get_calliope_appearance',user='member@example.com')
    assert status==200 and body['source']=='builtin' and body['mode']=='dark'
    status,_=request('workspace_admin_settings','PUT',body={'appearance':appearance(),'mode':'light'},headers={'x-calliope-admin':'1'})
    assert status==200, _
    status,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='workspace' and body['mode']=='light' and body['personal_mode'] is None
    with factory() as c:assert c.execute('SELECT count(*) AS n FROM rvbbit.calliope_appearance_preferences').fetchone()['n']==0
    request('put_calliope_appearance','PUT','member@example.com',{'mode':'dark'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='workspace' and body['mode']=='dark'
    personal=appearance();personal['tokens']={'--main':'#123456'}
    request('put_calliope_appearance','PUT','member@example.com',{'appearance':personal,'mode':'dark'})
    request('workspace_admin_settings','PUT',body={'appearance':None,'mode':'light'},headers={'x-calliope-admin':'1'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='personal' and body['appearance']['tokens']==personal['tokens']
    request('put_calliope_appearance','PUT','member@example.com',{'mode':'light'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='personal' and body['mode']=='light'
    request('put_calliope_appearance','PUT','member@example.com',{'appearance':None,'mode':'light'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='builtin' and body['mode']=='light'
    request('workspace_admin_settings','PUT',body={'appearance':appearance(),'mode':'dark'},headers={'x-calliope-admin':'1'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='workspace' and body['mode']=='light'


def test_admin_access_and_invalid_defaults(api):
    request,_=api
    for method in ['GET','PUT']:
        status,_=request('workspace_admin_settings',method,'member@example.com',{'appearance':appearance()}, {'x-calliope-admin':'1'})
        assert status==403
    assert request('workspace_admin_settings','PUT',body={})[0]==403
    assert request('workspace_admin_settings','PUT',body={},headers={'x-calliope-admin':'1','sec-fetch-site':'cross-site'})[0]==403
    for bad in [{**appearance(),'source':{'kind':'upload','name':'private.png'}},
                {**appearance(),'source':{'kind':'library','id':'missing','url':'/theme/images/full/missing.webp'}}]:
        assert request('workspace_admin_settings','PUT',body={'appearance':bad},headers={'x-calliope-admin':'1'})[0]==400
    assert request('workspace_admin_settings','PUT',body={'mode':'invalid'},headers={'x-calliope-admin':'1'})[0]==400
    assert request('workspace_admin_settings')[1]['appearance'] is None


def test_nullable_mode_and_migration_repeatable(api):
    request,factory=api
    request('put_calliope_appearance','PUT','member@example.com',{'appearance':appearance(),'mode':None})
    migration=Path(__file__).parents[2]/'crates/pg_rvbbit/sql/migrations/0416_workspace_appearance.sql'
    with factory() as c:
        c.execute(migration.read_text());c.execute(migration.read_text())
        assert c.execute('SELECT color_mode FROM rvbbit.calliope_appearance_preferences').fetchone()['color_mode'] is None
    request('workspace_admin_settings','PUT',body={'mode':'light'},headers={'x-calliope-admin':'1'})
    _,body=request('get_calliope_appearance',user='member@example.com')
    assert body['source']=='personal' and body['mode']=='light'
