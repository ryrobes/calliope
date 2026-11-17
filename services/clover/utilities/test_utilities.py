import base64
import importlib.util
import io
import json
from pathlib import Path
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

spec = importlib.util.spec_from_file_location("clover_utilities", Path(__file__).with_name("app.py"))
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b'\0\0' * 160)
    return out.getvalue()


def call(client, name, inputs):
    return client.post('/specialists/' + name + '/predict', json={'inputs': inputs})


@pytest.fixture(scope='module')
def language_client():
    with TestClient(worker.create_app(api_key='')) as client:
        yield client


def test_language_existing_sql_fixtures_and_multilingual(language_client):
    samples = [('bonjour mes amis','fr'), ('guten morgen alle zusammen','de'), ('buenos dias, como estas hoy','es'),
               ('The air conditioner needs repair','en'), ('请明天下午来维修空调','zh'), ('明日の午後に修理をお願いします','ja'),
               ('يرجى إصلاح مكيف الهواء غداً','ar')]
    result = call(language_client, 'language', [s[0] for s in samples])
    assert result.status_code == 200
    outputs = result.json()['outputs']
    assert [o['language'] for o in outputs] == [s[1] for s in samples]
    assert all(0 <= o['confidence'] <= 1 and o['usage']['cost'] == 0 for o in outputs)
    assert language_client.get('/health').status_code == 200
    assert language_client.get('/health/transcribe').status_code == 503


@pytest.mark.parametrize('mode,expected', [('ready',200),('unauthorized',503),('missing_model',503),('wrong_modality',503),('no_budget',503)])
def test_transcription_health_checks_provider_without_running_inference(mode,expected):
    seen=[]
    def upstream(request):
        seen.append(request)
        assert request.method=='GET' and request.headers['authorization']=='Bearer fixture'
        if request.url.path.endswith('/key'):
            return httpx.Response(401 if mode=='unauthorized' else 200,json={'data':{'limit_remaining':0 if mode=='no_budget' else None}})
        assert request.url.path.endswith('/models/user') and request.url.params['output_modalities']=='all'
        return httpx.Response(200,json={'data':[] if mode=='missing_model' else [{'id':worker.TRANSCRIBE_MODEL,'architecture':{'output_modalities':['text' if mode=='wrong_modality' else 'transcription']}}]})
    with TestClient(worker.create_app(api_key='fixture',transport=httpx.MockTransport(upstream))) as client:
        result=client.get('/health/transcribe')
        assert result.status_code==expected
        assert len(seen)==2
        if expected==200: assert result.json()['inference_verified'] is False


@pytest.mark.parametrize('item', ['', '123?!', {'text':'hello', 'top_k':3}, 'a'*32769, None])
def test_language_invalid_input(language_client, item):
    assert call(language_client,'language',[item]).status_code == 422


def test_url_data_uri_and_base64_use_same_transcription_contract():
    calls = []
    data = wav()
    def upstream(request):
        calls.append(request)
        if str(request.url) == worker.AUDIO_FETCH:
            assert 'authorization' not in request.headers
            assert json.loads(request.content) == {'url':'https://example.com/speech.wav'}
            return httpx.Response(200, content=data)
        assert str(request.url) == worker.ENDPOINT
        assert request.headers['authorization'] == 'Bearer test-provider-key'
        assert b'openai/gpt-transcribe' in request.content
        assert data in request.content
        return httpx.Response(200, json={'text':'The quick brown fox', 'usage':{'cost':0.000000123,'input_tokens':12,'secret':'omit'}})
    inputs = [{'audio':'https://example.com/speech.wav','language':'en'},
              'data:audio/wav;base64,'+base64.b64encode(data).decode(), base64.b64encode(data).decode()]
    with TestClient(worker.create_app(api_key='test-provider-key',transport=httpx.MockTransport(upstream))) as client:
        response = call(client,'transcribe',inputs)
        assert response.status_code == 200, response.text
        outputs = response.json()['outputs']
        assert [o['text'] for o in outputs] == ['The quick brown fox']*3
        assert outputs[0]['language'] == 'en' and outputs[1]['language'] is None
        assert all(o['duration_s'] == .01 and o['chunks'] == [] for o in outputs)
        assert all(o['usage']['cost'] == .000000123 and 'secret' not in o['usage'] for o in outputs)
        assert len(calls) == 4


@pytest.mark.parametrize('bad', ['/etc/passwd', 'data:text/plain;base64,aGVsbG8=', {'audio':'https://example.com/a.wav','task':'translate'}, {'audio':'https://example.com/a.wav','return_timestamps':True}, {'audio':'https://example.com/a.wav','language':False}])
def test_invalid_batch_spends_nothing(bad):
    calls=[]
    def upstream(request):
        calls.append(request)
        raise AssertionError('Invalid input reached provider')
    with TestClient(worker.create_app(api_key='test',transport=httpx.MockTransport(upstream))) as client:
        response=call(client,'transcribe',['https://example.com/valid.wav',bad])
        assert response.status_code == 422
    assert not calls


@pytest.mark.parametrize('status,body,expected', [(200,{'missing':'transcript'},502),(403,{'key':'must-not-echo'},502),(429,{},429)])
def test_provider_failures_not_transcripts(status,body,expected):
    with TestClient(worker.create_app(api_key='test',transport=httpx.MockTransport(lambda r:httpx.Response(status,json=body)))) as client:
        response=call(client,'transcribe',[base64.b64encode(wav()).decode()])
        assert response.status_code == expected
        assert 'must-not-echo' not in response.text and 'outputs' not in response.json()


def test_missing_cost_stays_unknown():
    out=worker.result_from_provider({'text':'a'},None,wav(),'test/model')
    assert 'cost' not in out['usage']
    assert out['duration_s'] == .01


def test_remote_html_does_not_reach_provider():
    calls=[]
    def upstream(request):
        calls.append(request)
        return httpx.Response(200,content=b'<html>not an audio file</html>')
    with TestClient(worker.create_app(api_key='test',transport=httpx.MockTransport(upstream))) as client:
        assert call(client,'transcribe',['https://example.com/not-audio']).status_code == 422
    assert len(calls) == 1


def test_capacity_and_request_limits(language_client):
    assert call(language_client,'language',['hello']*9).status_code == 422
    assert call(language_client,'unknown',['hello']).status_code == 404
    response=language_client.post('/specialists/transcribe/predict',content=b'x'*8_000_001,headers={'content-type':'application/json'})
    assert response.status_code == 413


def test_queue_is_bounded_and_released_after_provider_failure():
    import asyncio
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider(request):
            entered.set()
            await release.wait()
            return httpx.Response(503)
        app=worker.create_app(api_key='test',transport=httpx.MockTransport(provider),concurrency=1,queue_capacity=0)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                body={'inputs':[base64.b64encode(wav()).decode()]}
                first=asyncio.create_task(client.post('/specialists/transcribe/predict',json=body))
                await asyncio.wait_for(entered.wait(),1)
                overflow=await client.post('/specialists/transcribe/predict',json=body)
                assert overflow.status_code == 429
                release.set()
                assert (await first).status_code == 502
                assert app.state.active == 0
                assert (await client.post('/specialists/language/predict',json={'inputs':['bonjour mes amis']})).status_code == 200
    asyncio.run(exercise())


def test_shared_key_rotation_and_fail_closed(tmp_path,monkeypatch):
    path=tmp_path/'key';path.write_text('first')
    monkeypatch.setenv('OPENROUTER_API_KEY_FILE',str(path));monkeypatch.setenv('OPENROUTER_API_KEY','stale-env')
    headers=[]
    def provider(r):
        headers.append(r.headers['authorization']);return httpx.Response(200,json={'text':'test'})
    with TestClient(worker.create_app(transport=httpx.MockTransport(provider))) as client:
        for key in ['first','second']:
            path.write_text(key)
            assert call(client,'transcribe',[base64.b64encode(wav()).decode()]).status_code==200
        path.unlink()
        assert call(client,'transcribe',[base64.b64encode(wav()).decode()]).status_code==503
    assert headers==['Bearer first','Bearer second']
