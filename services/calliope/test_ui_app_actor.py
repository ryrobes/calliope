"""Authenticated SQL inputs cannot be supplied or overridden by an app client."""
import contextvars
import copy
import json
from types import SimpleNamespace

import pytest
import app_actions
import ui_apps
import ui_app_preview


def document():
    return {'schema':'calliope.app.v1', 'title':'My work',
        'components':[{'id':'root','component':'CText','text':{'path':'/owner'}}],
        'data':{'state':{'actor':'spoof@example.test'},'owner':''},
        'queries':{'identity':{'sql':'SELECT $1::text AS owner', 'params':[{'actor':True}],
                               'path':'/owner','shape':'value','cacheMs':5000}}}


def test_actor_binding_ignores_client_state_and_separates_cache():
    doc=document(); ui_apps.validate(doc)
    calls=[]
    def read(statement, **kwargs):
        calls.append(statement)
        return {'rows':[{'owner':statement}],'row_count':1}
    server=SimpleNamespace(tool_run_sql=read, _load_live_app_version=lambda *_: (
        {'name':'My work'}, {'kind':'a2ui','version':1,'source_files':{'app.json':json.dumps(doc)}}))
    ui_apps._QUERY_CACHE.clear()
    for actor in ['tech@example.test','dispatcher@example.test','tech@example.test']:
        result=ui_apps.snapshot(server,'actor-test',actor,{'state':doc['data']['state']})
        assert actor in result['messages'][2]['updateDataModel']['value']['owner']
        assert 'spoof' not in result['messages'][2]['updateDataModel']['value']['owner']
    assert len(calls)==2
    with pytest.raises(ValueError): ui_apps.snapshot(server,'actor-test','tech',{'actor':'dispatcher'})
    with pytest.raises(app_actions.ActionError): ui_apps.bound_sql(doc['queries']['identity'],doc['data'])
    assert "o''hara@example.test" in ui_apps.bound_sql(doc['queries']['identity'],doc['data'],"o'hara@example.test")


def test_preview_actor_comes_only_from_authenticated_boundary():
    calls=[]
    def read(statement, **kwargs):
        calls.append(statement);return {'rows':[{'owner':'tech'}],'row_count':1}
    server=SimpleNamespace(_SESSION_SUB=contextvars.ContextVar('actor'),tool_run_sql=read)
    ui_app_preview.request_snapshot(server,document(),{'mode':'server-read'},'tech@example.test')
    assert 'tech@example.test' in calls[0] and 'spoof' not in calls[0]
    with pytest.raises(ValueError):
        ui_app_preview.request_snapshot(server,document(),{'mode':'server-read','actor':'other'},'tech')
    assert not server._SESSION_SUB.get(None)


@pytest.mark.parametrize('param',[{'actor':False},{'actor':1},{'actor':True,'path':'/state/actor'}])
def test_invalid_actor_bindings_fail_before_publish(param):
    doc=document();doc['queries']['identity']['params']=[param]
    with pytest.raises(ValueError):ui_apps.validate(doc)


def test_actor_does_not_create_query_dependencies_or_navigation_paths():
    doc=document()
    doc['data']['state']['draft']={'id':0}
    doc['data']['record']={'id':0}
    doc['queries']['record']={'sql':'SELECT 1 AS id WHERE $1 IS NOT NULL','params':[{'actor':True}], 'path':'/record','shape':'first'}
    doc['forms']=[{'path':'/state/draft','source':'/record','key':'id'}]
    ui_apps.validate(doc)
    assert ui_apps.navigation(doc)['paths']==[]
    assert ui_apps.query_order(doc['queries'])==[('identity',set()),('record',set())]
