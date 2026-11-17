"""Transport retries must never submit a second SMS after an ambiguous result."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

spec=importlib.util.spec_from_file_location('sms_bridge',Path(__file__).parents[2]/'docker/calliope/sms_bridge.py')
bridge=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
ACCOUNT={'account_sid':'AC'+'a'*32,'auth_token':'secret','phone_number':'+13215550000','webhook_url':'https://example.com/webhooks/twilio'}


@pytest.mark.parametrize('result,state',[(SimpleNamespace(success=True,message_id='SM123',error=None),'sent'),
    (SimpleNamespace(success=False,message_id=None,error='Twilio 400: bad number'),'failed'),
    (SimpleNamespace(success=False,message_id=None,error='timeout'),'uncertain')])
def test_retries_are_idempotent_and_recipient_is_frozen(tmp_path,monkeypatch,result,state):
    calls=[]
    class Native:
        async def send(self,to,text):
            calls.append((to,text));return result
    monkeypatch.setattr(bridge,'adapter',lambda account:Native())
    request={'account':ACCOUNT,'attempt_id':'test-1','to':'+13215550123','text':'Hello'}
    first=bridge.send(request,spool=tmp_path/'sms.sqlite')
    assert first['state']==state
    assert bridge.send(request,spool=tmp_path/'sms.sqlite')==first
    assert calls==[('+13215550123','Hello')]
    with pytest.raises(ValueError,match='changed'):
        bridge.send({**request,'to':'+13215550456'},spool=tmp_path/'sms.sqlite')


def test_crash_during_submit_is_uncertain_and_not_repeated(tmp_path,monkeypatch):
    calls=[]
    class Native:
        async def send(self,*args):
            calls.append(args);raise KeyboardInterrupt('simulated process stop')
    monkeypatch.setattr(bridge,'adapter',lambda account:Native())
    request={'account':ACCOUNT,'attempt_id':'test-1','to':'+13215550123','text':'Hello'}
    with pytest.raises(KeyboardInterrupt):bridge.send(request,spool=tmp_path/'sms.sqlite')
    assert bridge.send(request,spool=tmp_path/'sms.sqlite')['state']=='uncertain'
    assert len(calls)==1


def test_media_rejects_non_twilio_and_other_account_urls_before_fetch(monkeypatch):
    import aiohttp
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def get(self,*args,**kwargs):raise AssertionError('Unexpected network request')
    monkeypatch.setattr(aiohttp,'ClientSession',lambda **kwargs:Client())
    for url in ['http://127.0.0.1/secret','https://api.twilio.com.evil.com/image',
                'https://api.twilio.com/2010-04-01/Accounts/AC'+'b'*32+'/Messages/SM'+'c'*32+'/Media/ME'+'d'*32]:
        with pytest.raises(ValueError,match='Invalid Twilio media'):
            bridge.media({'account':ACCOUNT,'message_sid':'SM'+'c'*32,'media':[{'url':url}]})


def test_media_redirect_never_forwards_twilio_credentials(monkeypatch):
    import aiohttp
    from unittest.mock import AsyncMock
    requests=[]
    class Content:
        async def iter_chunked(self,n):yield b'example-image'
    class Response:
        content=Content()
        def __init__(self,status,headers):self.status=status;self.headers=headers
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
    class Client:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def get(self,url,**kwargs):
            requests.append((url,kwargs))
            return Response(302,{'Location':'https://mms.twiliocdn.com/image'}) if len(requests)==1 else Response(200,{'Content-Type':'image/jpeg'})
    monkeypatch.setattr(aiohttp,'ClientSession',lambda **kwargs:Client())
    url='https://api.twilio.com/2010-04-01/Accounts/'+ACCOUNT['account_sid']+'/Messages/SM'+'c'*32+'/Media/ME'+'d'*32
    result=bridge.media({'account':ACCOUNT,'message_sid':'SM'+'c'*32,'media':[{'url':url}]})
    assert result['attachments'][0]['data_url'].startswith('data:image/jpeg;base64,')
    assert requests[0][1]['auth'] and requests[1][1]['auth'] is None
