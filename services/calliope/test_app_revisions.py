import contextvars
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

import pytest
import app_revisions as revisions
import artifact_access

ID='9554fcce-190d-45c5-a239-b9d1f92ab054'

def test_request_query_has_author_and_explicit_share_boundaries():
    factory=MagicMock();conn=factory.return_value.__enter__.return_value
    conn.execute.return_value.fetchone.return_value={'id':1,'name':'App','owner_email':'author@test','latest_version':2}
    conn.execute.return_value.fetchall.side_effect=[[
        {'id':ID,'user_message':'Please improve','origin_context':{'version':1},'requested_by':'worker@test'}],
        [{'slug':'app','name':'App','version':2,'notes':'Changed'}]]
    with patch('app_revisions.ensure'),patch('app_review.decorate',side_effect=lambda c,a,r:r):
        data=revisions.requests(factory,'worker@test','app')
    queries=conn.execute.call_args_list
    assert 'artifact_can_view' in queries[0].args[0]
    assert "origin_context->>'intent'='improve'" in queries[1].args[0]
    assert 'coalesce(t.author_email,s.owner_email)' in queries[1].args[0]
    assert queries[1].args[1][-3:]==('worker@test','author@test','worker@test')
    assert 'assistant_message' not in queries[1].args[0]
    assert 'artifact_can_view' in queries[2].args[0]
    assert data['requests'][0]['status']=='published'


def test_cannot_link_private_turn_or_other_authors_request():
    conn=MagicMock();conn.execute.return_value.fetchone.return_value=None
    with pytest.raises(ValueError,match='unavailable'):revisions.validate_requests(conn,'author@test',{'change_requests':[ID]})
    sql,args=conn.execute.call_args.args
    assert "intent'='improve'" in sql and 'lower(d.owner_email)=lower(%s)' in sql
    assert args==(ID,'author@test')
    for ids in [[ID]*21,'oops',['not-a-uuid']]:
        with pytest.raises(ValueError):revisions.validate_requests(conn,'author@test',{'change_requests':ids})


def test_pinned_snapshot_explains_latest_update_without_changing_version():
    old={'version':2,'notes':'Old','manifest':{}}
    new={'version':3,'notes':'Follow-up reason','manifest':{'change_requests':[ID]}}
    server=SimpleNamespace(_load_live_app_version=lambda slug:({},new))
    result=revisions.release(server,'visits',{'latest_version':3},old)
    assert old['version']==2
    assert result['version']==3 and result['notes']=='Follow-up reason'
    assert result['requests']==[ID] and 'before=2&after=3' in result['compare_url']


def test_revoked_access_rejected_before_cached_image(tmp_path):
    server=SimpleNamespace(_artifact_require_view=MagicMock(side_effect=artifact_access.ArtifactAccessError('DENIED','No access',403)))
    with pytest.raises(artifact_access.ArtifactAccessError):
        revisions.capture(server,'viewer@test','visits',1,'phone','web')


def test_native_comparison_uses_readonly_preview_and_private_cache(tmp_path):
    token=contextvars.ContextVar('actor',default=None)
    row={'kind':'a2ui','version':2,'source_files':{'app.json':'{}'}}
    server=SimpleNamespace(_artifact_require_view=MagicMock(),_load_live_app_version=lambda *a:({'name':'App'},row),
        _live_app_capture_root=lambda:tmp_path,_SESSION_SUB=token,_conn=MagicMock())
    import base64
    with patch('ui_apps.definition',return_value={}),patch('ui_app_preview.request_snapshot',return_value={'preview':{'ready':True}}) as snapshot,patch('artifact_app_preview.theme_for',return_value={}),patch('ui_app_captures.native',return_value={'captures':{'phone':{'ready':True,'data':base64.b64encode(b'PNG').decode()}}}) as native:
        path=revisions.capture(server,'viewer@test','visits',2,'phone','native')
        assert snapshot.call_args.args[2]=={'mode':'server-read'}
        assert snapshot.call_args.args[3]=='viewer@test'
        assert path.read_bytes()==b'PNG' and path.stat().st_mode&0o777==0o600
        assert token.get() is None
        assert revisions.capture(server,'viewer@test','visits',2,'phone','native')==path
        assert native.call_count==1
        # Same version and renderer for another viewer cannot reuse the image.
        assert revisions.capture(server,'other@test','visits',2,'phone','native')!=path
        assert native.call_count==2


def test_record_comparison_uses_declared_navigation_and_caller_reads(tmp_path):
    doc={'data':{'state':{'id':'','draft':{}}},'records':{'visit':{
        'id':{'path':'/record/id'},'open':{'/state/id':{'recordId':True}}}}}
    row={'kind':'a2ui','version':2,'source_files':{'app.json':json.dumps(doc)}}
    server=SimpleNamespace(_artifact_require_view=MagicMock(),_load_live_app_version=lambda *a:({'name':'App'},row),
        _live_app_capture_root=lambda:tmp_path,_SESSION_SUB=contextvars.ContextVar('actor',default=None),
        _conn=MagicMock(),_launch_playwright_chromium=None)
    value={'preview':{'ready':True},'messages':[{}, {}, {'updateDataModel':{'value':{'record':{'id':'V1'}}}}]}
    def image(snapshot,path,*args,**kwargs):
        assert kwargs['full_page'] is True
        path.write_bytes(b'PNG');return {'ready':True}
    with patch('ui_apps.definition',return_value=doc),patch('ui_app_preview.request_snapshot',return_value=value) as snapshot,patch('artifact_app_preview.theme_for',return_value={}),patch('ui_app_captures.web',side_effect=image):
        ref={'slug':'visits','record':'visit','id':'V1'}
        revisions.capture(server,'worker@test','visits',2,'phone','web',ref)
        assert snapshot.call_args.args[2]=={'mode':'server-read','state':{'id':'V1','draft':{}}}
        assert snapshot.call_args.args[3]=='worker@test'
        with pytest.raises(ValueError,match='another app'):
            revisions.capture(server,'worker@test','visits',2,'phone','web',ref|{'slug':'other'})
        # An actor-scoped read that does not resolve that record cannot be passed off as ready.
        value['messages'][2]['updateDataModel']['value']['record']['id']=None
        with pytest.raises(ValueError,match='unavailable'):
            revisions.capture(server,'other-worker@test','visits',2,'phone','web',ref)


def test_machine_attribution_without_human_subject_cannot_read_requests():
    factory=MagicMock()
    for actor in (None,'', '   '):
        with pytest.raises(ValueError,match='signed-in'):
            revisions.requests(factory,actor,'visits')
    factory.assert_not_called()
