"""The spoken writer and delivery director are independent model calls."""
import json
from dataclasses import replace

import httpx
import pytest

import voice_tagging
from test_calliope_voice import calliope


TAKE = "The dashboard is ready. Revenue rose 12 percent, but two invoices still need checking."
TAGGED = "[pleased] The dashboard is ready. Revenue rose [emphasis] 12 percent, [serious] but two invoices still need checking."


def config(tmp_path):
    return calliope.CalliopeConfig(hermes_url="http://hermes:8642", hermes_api_key="writer-secret",
        memory_key="company", file_root=tmp_path, max_image_bytes=1024,
        voice_rewrite_api_key="clover-secret", voice_rewrite_base_url="https://clover.example/v1")


def mock_transport(monkeypatch, handler):
    client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("tag_model", ["calliope-luna", "calliope-sol"])
def test_two_sequential_calls_with_independent_models_and_receipts(monkeypatch, tmp_path, tag_model):
    requests = []
    monkeypatch.setenv("CALLIOPE_LEDGER_DIR", str(tmp_path))
    def handler(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        if len(requests) == 1:
            assert request.url.path == "/v1/auxiliary/tts-rewrite"
            assert request.headers["authorization"] == "Bearer writer-secret"
            assert "model" not in payload
            return httpx.Response(200, json={"provider": "custom", "model": "writer-model",
                "choices": [{"finish_reason": "stop", "message": {"content": TAKE}}]})
        assert request.url == "https://clover.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer clover-secret"
        assert payload["model"] == tag_model
        assert json.loads(payload["messages"][1]["content"]) == {
            "script": TAKE, "personality": "Warm, dry humor", "delivery_direction": "quiet confidence"}
        return httpx.Response(200, headers={"x-hutch-upstream-model": "upstream/director"}, json={
            "model": tag_model, "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            "choices": [{"finish_reason": "stop", "message": {"content": TAGGED}}]})
    mock_transport(monkeypatch, handler)
    details = {}
    result = calliope._generate_voice_script(replace(config(tmp_path), voice_tag_model=tag_model),
        "Canonical answer before conversationalizing.", "expressive", "Warm, dry humor", "owner@example.com",
        direction="quiet confidence", details=details, session_id="test-session")
    assert result == (TAGGED, "custom", "writer-model")
    assert len(requests) == 2
    assert details["emotion_tags"]["requested_model"] == tag_model
    assert details["emotion_tags"]["model"] == "upstream/director"
    assert details["emotion_tags"]["status"] == "tagged"
    receipt = json.loads(next(tmp_path.glob('*.jsonl')).read_text())
    assert receipt["operation"] == "tts.emotion_tags" and receipt["session_id"] == "test-session"
    assert receipt["usage"]["prompt_tokens"] == 123
    assert "secret" not in json.dumps(receipt) and TAKE not in json.dumps(receipt)


def test_director_output_is_used_without_postprocessing_or_word_gate(monkeypatch, tmp_path):
    # This pass trusts the model. No tag counting, remapping, or word-diff rejection.
    output = "[warmly] It's ready now! [with a dry chuckle] Two invoices left."
    monkeypatch.setenv("CALLIOPE_LEDGER_DIR", str(tmp_path))
    mock_transport(monkeypatch, lambda r: httpx.Response(200, json={"choices": [
        {"finish_reason": "stop", "message": {"content": output}}]}))
    assert voice_tagging.tag(TAKE, model="calliope-luna", base_url="https://clover.example/v1",
                             api_key="secret") == output


def test_tagging_transport_failure_keeps_the_spoken_take(monkeypatch, tmp_path):
    monkeypatch.setenv("CALLIOPE_LEDGER_DIR", str(tmp_path))
    mock_transport(monkeypatch, lambda r: httpx.Response(503, text="unavailable"))
    receipt = {}
    assert voice_tagging.tag(TAKE, model="calliope-luna", base_url="https://clover.example/v1",
                             api_key="secret", receipt=receipt) == TAKE
    assert receipt["status"] == "fallback"
    assert json.loads(next(tmp_path.glob('*.jsonl')).read_text())["status"] == "error"


def test_fast_mode_calls_only_the_writer(monkeypatch, tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": TAKE}}]})
    mock_transport(monkeypatch, handler)
    details = {}
    script, _, _ = calliope._generate_voice_script(config(tmp_path), TAKE, "fast", "", "owner@example.com", details=details)
    assert script == TAKE and len(requests) == 1
    assert details["emotion_tags"]["status"] == "disabled"


def test_tag_model_has_its_own_install_setting(monkeypatch):
    monkeypatch.delenv("WAREHOUSE_CALLIOPE_TTS_TAG_MODEL", raising=False)
    assert calliope.CalliopeConfig.from_env().voice_tag_model == "calliope-luna"
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_TTS_TAG_MODEL", "calliope-sol")
    assert calliope.CalliopeConfig.from_env().voice_tag_model == "calliope-sol"
