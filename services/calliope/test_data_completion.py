import io
import json
import urllib.error

import pytest

import data_completion as completion


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('WAREHOUSE_HERMES_URL', 'http://hermes:8642')
    monkeypatch.setenv('WAREHOUSE_HERMES_API_KEY', 'private-internal-key')
    return completion.DataCompletionClient('openai-codex', 'selected-model')


def test_forward_only_selected_route_and_text_and_retain_usage(client, monkeypatch):
    response = {'provider': 'openai-codex', 'model': 'selected-model', 'content': '{"sql":"SELECT 1"}',
                'receipt': {'usage': {'prompt_tokens': 100, 'completion_tokens': 20}, 'model_version': 'wire-model'}}
    def send(request, timeout):
        body = json.loads(request.data)
        assert body['provider'] == 'openai-codex' and body['model'] == 'selected-model'
        assert body['messages'] == [{'role': 'system', 'content': 'system'}, {'role': 'user', 'content': 'user'}]
        assert set(body) == {'provider', 'model', 'messages', 'max_tokens', 'timeout_seconds'}
        assert timeout == 100
        assert request.headers['Authorization'] == 'Bearer private-internal-key'
        return io.BytesIO(json.dumps(response).encode())
    monkeypatch.setattr(completion.urllib.request, 'urlopen', send)
    answer, receipt = client.complete_json(system='system', user='user')
    assert json.loads(answer)['sql'] == 'SELECT 1'
    assert receipt['usage']['completion_tokens'] == 20


@pytest.mark.parametrize('status,payload', [
    (502, {'error': {'code': 'data_completion_empty', 'message': 'secret upstream error'}, 'receipt': {'usage': {'completion_tokens': 3}}}),
    (200, {'provider': 'openrouter', 'model': 'wrong-model', 'content': '{}'}),
    (200, {'provider': 'openai-codex', 'model': 'selected-model', 'content': ''}),
    (401, {'error': 'secret'}),
    (404, '<html>not found</html>'),
])
def test_failures_are_sanitized_and_do_not_retry_or_fallback(client, monkeypatch, status, payload):
    calls = []
    def send(request, timeout):
        calls.append(request)
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, 'secret', {}, io.BytesIO(raw.encode()))
        return io.BytesIO(raw.encode())
    monkeypatch.setattr(completion.urllib.request, 'urlopen', send)
    with pytest.raises(completion.CompletionError) as exc:
        client.complete_json(system='s', user='u')
    assert 'secret' not in str(exc.value)
    assert len(calls) == 1
    if status == 502:
        assert exc.value.receipt['usage']['completion_tokens'] == 3


def test_missing_bridge_auth_fails_closed(monkeypatch):
    monkeypatch.delenv('WAREHOUSE_HERMES_API_KEY', raising=False)
    with pytest.raises(ValueError):
        completion.DataCompletionClient('openrouter', 'selected-model')
