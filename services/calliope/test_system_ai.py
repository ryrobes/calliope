import json
import httpx
import pytest
import system_ai


def test_key_validation_atomic_rotation_and_no_secret_in_status(tmp_path,monkeypatch):
    path=tmp_path/'openrouter.key';path.write_text('old-key')
    monkeypatch.setenv('CALLIOPE_OPENROUTER_KEY_FILE',str(path))
    seen=[]
    def provider(request):
        seen.append(request.headers['authorization'])
        return httpx.Response(200,json={'data':{'label':'not copied','limit_remaining':10}})
    result=system_ai.save_key('new-key',transport=httpx.MockTransport(provider))
    assert seen==['Bearer new-key'] and path.read_text().strip()=='new-key'
    assert result['configured'] and result['editable']
    assert 'new-key' not in json.dumps(result) and 'not copied' not in json.dumps(result)
    rejected=httpx.MockTransport(lambda r:httpx.Response(401,json={'error':'private body'}))
    with pytest.raises(ValueError,match='did not accept'):system_ai.save_key('rejected-key',transport=rejected)
    assert path.read_text().strip()=='new-key'
    assert list(tmp_path.iterdir())==[path]
    system_ai.check_key(transport=httpx.MockTransport(provider))
    assert seen[-1]=='Bearer new-key'


def test_managed_key_needs_explicit_deployment_mount(monkeypatch):
    monkeypatch.delenv('CALLIOPE_OPENROUTER_KEY_FILE',raising=False)
    monkeypatch.setenv('OPENROUTER_API_KEY','unrelated-chat-key')
    assert system_ai.status()['configured'] is False
    with pytest.raises(ValueError,match='not enabled'):system_ai.save_key('unused')


def test_hermes_sync_uses_mount_and_reports_partial_failure(monkeypatch,tmp_path):
    import provider_settings
    path=tmp_path/'openrouter.key';path.write_text('old')
    monkeypatch.setenv('CALLIOPE_OPENROUTER_KEY_FILE',str(path))
    calls=[];refreshes=[]
    def control(method,endpoint,body,**kwargs):
        calls.append((method,endpoint,body))
        assert path.read_text().strip()=='replacement'
        return {'ok':True,'shared':True,'restarted':True}
    monkeypatch.setattr(provider_settings,'_control',control)
    monkeypatch.setattr(provider_settings,'refresh',lambda *a,**k: refreshes.append(k))
    result=system_ai.save_key('replacement',transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':{}})),conn_factory=lambda: None)
    assert result['hermes']['ok'] and result['hermes']['restarted']
    assert calls==[('POST','/v1/providers/instance-key',{})] and refreshes==[{'models':True}]
    assert 'replacement' not in json.dumps(result)
    monkeypatch.setattr(provider_settings,'_control',lambda *a,**kw: (_ for _ in ()).throw(RuntimeError('secret diagnostic')))
    result=system_ai.check_key(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':{}})))
    assert result['configured'] and not result['hermes']['ok'] and 'pending' in result['message']
    assert path.read_text().strip()=='replacement' and 'secret diagnostic' not in json.dumps(result)
