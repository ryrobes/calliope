import asyncio
import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

import app_review as review
from app_actions import ActionError

ID='9554fcce-190d-45c5-a239-b9d1f92ab054'


def row():
    return {'id':ID,'owner_email':'author@test','requested_by':'worker@test','releases':[{'version':2}],
        'review':{'revision':1,'can_triage':False,'can_confirm':True,'release_key':'new'},'origin_context':{}}


def test_new_release_reopens_confirmation_consistently_for_both_parties():
    connection=MagicMock()
    connection.execute.return_value.fetchone.return_value={'revision':1,'owner_status':'working',
        'requester_status':'fixed','release_key':'old'}
    connection.execute.return_value.fetchall.return_value=[{'dashboard_id':1,'version':3}]
    for actor in ['author@test','worker@test']:
        result=review.decorate(connection,actor,row())
        assert result['status']=='published' and result['review']['requester_status']==''
        key=result['review']['release_key']
    connection.execute.return_value.fetchone.return_value['release_key']=key
    assert review.decorate(connection,'worker@test',row())['status']=='fixed'


def test_owner_cannot_confirm_on_requesters_behalf_and_stale_release_conflicts(monkeypatch):
    server=SimpleNamespace(_conn=MagicMock())
    item=row();item['review']['can_confirm']=False;item['review']['can_triage']=True
    monkeypatch.setattr(review.app_revisions,'requests',lambda *a:{'requests':[item]})
    with pytest.raises(ActionError) as error:
        review.update_review(server,'author@test','visits',ID,{'revision':1,'requester_status':'fixed','release_key':'new'})
    assert error.value.status==403
    item['review']['can_confirm']=True
    with pytest.raises(ActionError) as error:
        review.update_review(server,'worker@test','visits',ID,{'revision':1,'requester_status':'fixed','release_key':'old'})
    assert error.value.status==409
    server._conn.assert_not_called()


def test_review_write_checks_locked_revision(monkeypatch):
    server=SimpleNamespace(_conn=MagicMock());item=row()
    monkeypatch.setattr(review.app_revisions,'requests',lambda *a:{'requests':[item]})
    conn=server._conn.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value={'revision':2}
    with pytest.raises(ActionError) as error:
        review.update_review(server,'worker@test','visits',ID,{'revision':1,'requester_status':'reopened','release_key':'new'})
    assert error.value.status==409
    assert not any('UPDATE rvbbit' in c.args[0] for c in conn.execute.call_args_list)


def test_routes_reject_cross_origin_writes_and_require_authentication(monkeypatch):
    routes=[]
    class Registry:
        def custom_route(self,path,methods):
            def register(fn):routes.append(Route(path,fn,methods=methods));return fn
            return register
    def actor(request):
        if request.headers.get('authorization')!='test': return None,None,JSONResponse({},401)
        return 'worker@test',{'sub':'worker@test'},None
    changed=[]
    monkeypatch.setattr(review,'update_review',lambda *args:changed.append(args) or {'ok':True})
    with ThreadPoolExecutor(max_workers=1) as pool:
        review.register(Registry(),SimpleNamespace(_CONTROL_EXECUTOR=pool),actor,lambda data,status=200:JSONResponse(data,status))
        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)),base_url='https://calliope.test') as client:
                path=f'/api/apps/visits/changes/{ID}/review'
                assert (await client.post(path,json={})).status_code==401
                assert (await client.post(path,json={},headers={'authorization':'test','origin':'https://evil.test'})).status_code==403
                assert not changed
                result=await client.post(path,json={'revision':1},headers={'authorization':'test','origin':'https://calliope.test'})
                assert result.status_code==200 and 'no-store' in result.headers['cache-control']
        asyncio.run(check())
