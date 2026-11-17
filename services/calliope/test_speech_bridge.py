import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location('speech_bridge_test', Path(__file__).parents[2] / 'docker/calliope/speech_bridge.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


@pytest.fixture
def speech(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.setattr(bridge, '_key', lambda: 'never-expose-this-key')
    return tmp_path


def test_settings_do_not_touch_agents_and_reject_stale_writes(speech):
    agent = speech / 'config.yaml'
    agent.write_text('model: my-model\n')
    info = bridge.settings()
    assert 'never-expose' not in json.dumps(info)
    assert info['enabled']
    result = bridge.save({'provider':'off', 'model':'gpt-live-transcribe','revision':info['revision']})
    assert not result['enabled']
    assert agent.read_text() == 'model: my-model\n'
    with pytest.raises(ValueError, match='changed'):
        bridge.save({'provider':'openai-api','model':'gpt-live-transcribe','revision':info['revision']})
    assert 'never-expose' not in (speech/'calliope-speech.json').read_text()


def test_realtime_forces_transcription_only_and_keeps_sdp_terminator(speech, monkeypatch):
    sent = {}
    def post(path, **kwargs):
        sent.update(path=path, **kwargs)
        return SimpleNamespace(text='v=0\r\nm=audio\r\n')
    monkeypatch.setattr(bridge, '_post', post)
    result = bridge.realtime({'sdp':'v=0\r\nm=audio\r\n', 'keywords':['Salesforce'],
                              'model':'untrusted', 'url':'https://untrusted', 'type':'realtime'})
    assert result['sdp'].endswith('\r\n')
    session = json.loads(sent['files']['session'][1])
    assert session['type'] == 'transcription'
    assert session['audio']['input']['turn_detection'] is None
    assert session['audio']['input']['transcription']['model'] == 'gpt-live-transcribe'
    assert sent['path'] == 'realtime/calls'
    with pytest.raises(ValueError):
        bridge.realtime({'sdp':'v=0\r\nm=video'})


def test_silence_has_actionable_message_and_audio_is_not_retained(speech, monkeypatch):
    monkeypatch.setattr(bridge, '_post', lambda *a, **k: SimpleNamespace(json=lambda:{'text':''}))
    before = list(speech.iterdir())
    with pytest.raises(ValueError, match='microphone'):
        bridge.transcribe({'audio':base64.b64encode(b'x'*64).decode(),'extension':'webm'})
    assert list(speech.iterdir()) == before


def test_provider_credentials_resolve_per_request_and_never_enter_response(speech, monkeypatch):
    credentials = iter(['old-private-key', 'rotated-private-key'])
    monkeypatch.setattr(bridge, '_key', lambda: next(credentials))
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs['follow_redirects'] is False
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return SimpleNamespace(status_code=201, text='v=0\r\nm=audio\r\n')
    monkeypatch.setattr(bridge.httpx, 'Client', Client)
    for _ in range(2):
        result = bridge.realtime({'sdp':'v=0\r\nm=audio\r\n'})
        assert 'private-key' not in json.dumps(result)
    assert [c[1]['headers']['Authorization'] for c in calls] == ['Bearer old-private-key','Bearer rotated-private-key']
    assert all(c[0] == 'https://api.openai.com/v1/realtime/calls' for c in calls)


def test_clover_defaults_hide_upstream_selection(speech,monkeypatch):
    monkeypatch.setenv('RVBBIT_CLOVER_KEY','secret')
    assert bridge.settings()['provider']=='clover'
    assert bridge.settings()['model']=='calliope-stt'
    assert not bridge.settings()['realtime']['enabled']
    sent={}
    def post(path,**kw):
        sent.update(path=path,**kw)
        return SimpleNamespace(json=lambda:{'text':'hello'})
    monkeypatch.setattr(bridge,'_post',post)
    assert bridge.transcribe({'audio':base64.b64encode(b'x'*64).decode(),'extension':'wav'})['text']=='hello'
    assert sent['data']['model']=='calliope-stt'
