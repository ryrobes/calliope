"""Exact Playbook versions remain permission checked at composer submission."""
import pytest
import calliope
import playbook_access

BOOK='10000000-0000-4000-8000-000000000001'

def test_reference_roundtrip_resolves_identity_and_version_not_client_label(monkeypatch):
    calls=[]
    def read(factory,owner,ref,version):
        calls.append((owner,ref,version))
        return {'playbook':{'id':BOOK,'version':3,'title':'Trusted title','synopsis':'Method summary','capability_name':'composition','approved':False}}
    monkeypatch.setattr(playbook_access,'read',read)
    markers=calliope._composer_object_markers(f'Use [[playbook:{BOOK}@3|Client title]]')
    refs=calliope._resolve_composer_objects(None,'owner@test',markers)
    assert calls==[('owner@test',BOOK,3)]
    assert refs[0]['label']=='Trusted title'
    assert refs[0]['handle']=={'kind':'playbook','id':BOOK,'version':3,'capability_name':'composition','approved':False}
    assert refs[0]['ref_id']==BOOK+'@3'
    context=calliope._composer_object_context_text(refs)
    assert 'read_calliope_playbook' in context and BOOK in context
    assert calliope._decode_composer_object_handles([{'kind':'playbook','ref_id':BOOK+'@3'}])[0]['kind']=='playbook'

def test_revoked_playbook_is_rejected_at_send(monkeypatch):
    def denied(*args):raise playbook_access.PlaybookError('PLAYBOOK_NOT_FOUND','Unavailable',404)
    monkeypatch.setattr(playbook_access,'read',denied)
    with pytest.raises(ValueError,match='no longer available'):
        calliope._resolve_composer_objects(None,'other@test',[{'kind':'playbook','ref_id':BOOK+'@3'}])

@pytest.mark.parametrize('ref',[BOOK,BOOK+'@0','not-a-uuid@1'])
def test_playbook_requires_exact_valid_version(ref):
    with pytest.raises(ValueError,match='unavailable'):
        calliope._resolve_composer_objects(None,'owner@test',[{'kind':'playbook','ref_id':ref}])


def test_stale_dashboard_client_normalizes_to_html_and_picker_assets_are_private(monkeypatch,tmp_path):
    import asyncio
    import json
    import sys
    from types import SimpleNamespace
    routes={};writes=[]
    class MCP:
        @staticmethod
        def custom_route(path,methods):
            def register(handler):routes[(path,tuple(methods))]=handler;return handler
            return register
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def execute(self,sql,params):
            writes.append((sql,params))
            return SimpleNamespace(fetchone=lambda:{'id':BOOK,'owner_email':'owner@test','authoring_mode':params[0]})
    monkeypatch.setitem(sys.modules,'auth',SimpleNamespace(read_session_full=lambda request:request.session,google_enabled=lambda:False,background_layer=lambda *a,**k:''))
    monkeypatch.setattr(calliope,'ensure_tables',lambda f:None)
    monkeypatch.setattr(calliope,'_start_session_synopsis_worker',lambda f:None)
    monkeypatch.setattr(calliope.hermes_docs,'ensure',lambda f:None)
    monkeypatch.setattr(calliope.hermes_config,'ensure',lambda f:None)
    monkeypatch.setattr(calliope.provider_settings,'ensure',lambda f:None)
    monkeypatch.setattr(calliope,'_session_for_owner',lambda *a:{'id':BOOK,'owner_email':'owner@test','authoring_mode':'dashboard'})
    monkeypatch.setenv('WAREHOUSE_HERMES_URL','http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY','test-key')
    monkeypatch.setenv('WAREHOUSE_CALLIOPE_DIR',str(tmp_path))
    calliope.register_calliope_routes(MCP(),Connection,'',lambda doc:doc)
    async def body():return {'authoring_mode':'dashboard'}
    request=SimpleNamespace(session={'identity':'owner@test','sub':'analyst_role','mapped':True,'via':'google'},path_params={'session_id':BOOK},json=body)
    response=asyncio.run(routes[('/api/calliope/sessions/{session_id}',('PATCH',))](request))
    assert response.status_code==200
    assert json.loads(response.body)['session']['authoring_mode']=='html'
    assert writes[0][1][0]=='html'
    for extension in ['js','css']:
        path=f'/calliope/playbook-picker.{extension}'
        request=SimpleNamespace(session={},url=SimpleNamespace(path=path))
        assert asyncio.run(routes[(path,('GET',))](request)).status_code==401
