"""Focused contracts for review-first Calliope speech transcription."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest


HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "warehouse_calliope_speech_test_module", HERE / "calliope.py"
)
calliope = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = calliope
SPEC.loader.exec_module(calliope)


def test_realtime_hints_are_bounded_normalized_and_optional(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "hermes-key")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_KEY", "speech-key")
    monkeypatch.setenv(
        "WAREHOUSE_CALLIOPE_STT_KEYWORDS",
        " RVBBIT ; Calliope,rvbbit\n<unsafe>,X," + ("z" * 121),
    )
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_LANGUAGES", "EN, fr;zh-tw bad_code")

    config = calliope.CalliopeConfig.from_env()

    assert config.transcription_keywords == ("RVBBIT", "Calliope")
    assert config.transcription_languages == ("en", "fr", "zh-tw")


def test_realtime_context_hints_stay_inside_the_authenticated_users_graph():
    queries = []

    class Rows:
        def __init__(self, values):
            self.values = values

        def fetchall(self):
            return self.values

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, params):
            queries.append((query, params))
            if "calliope_sessions" in query:
                return Rows([
                    {"label": "Revenue review"},
                    {"label": "ENG-42"},
                ])
            return Rows([
                {"label": "Ada Lovelace"},
                {"label": "revenue review"},
                {"label": "z" * 121},
            ])

    config = calliope.CalliopeConfig(
        hermes_url="http://hermes:8642",
        hermes_api_key="hermes-key",
        memory_key="company",
        file_root=Path("/tmp/calliope-speech-test"),
        max_image_bytes=1024,
        transcription_keywords=("RVBBIT",),
    )
    session_id = "73ea6745-ee4f-48eb-bc8c-350fb99d096a"

    hints = calliope._transcription_context_keywords(
        Connection, config, "pilot@example.com", session_id
    )

    assert hints == ("RVBBIT", "Revenue review", "ENG-42", "Ada Lovelace")
    assert queries[0][1] == (
        session_id, "pilot@example.com", session_id, "pilot@example.com"
    )
    assert queries[1][1] == ("pilot@example.com", "pilot@example.com")


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"\x1aE\xdf\xa3" + b"\0" * 40, ("webm", "audio/webm")),
        (b"RIFF" + b"\0" * 4 + b"WAVE" + b"\0" * 32, ("wav", "audio/wav")),
        (b"\0\0\0\x18ftypM4A " + b"\0" * 32, ("m4a", "audio/mp4")),
        (b"ID3" + b"\0" * 40, ("mp3", "audio/mpeg")),
    ],
)
def test_audio_uploads_are_magic_byte_identified(payload, expected):
    assert calliope._transcription_audio_format(payload) == expected
    assert calliope._transcription_audio_format(b"not actually audio" * 3) is None


def test_transcription_route_is_owner_gated_and_closes_the_upload(monkeypatch, tmp_path):
    routes = {}

    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[(path, tuple(methods))] = handler
                return handler
            return register

    fake_auth = types.SimpleNamespace(
        read_session_full=lambda request: getattr(request, "session", None)
    )
    monkeypatch.setitem(sys.modules, "auth", fake_auth)
    monkeypatch.setattr(calliope, "ensure_tables", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_docs, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_config, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope.provider_settings, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope, "_start_session_synopsis_worker", lambda _factory: None)
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "hermes-key")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_DIR", str(tmp_path))
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_KEY", "speech-key")

    seen = {}

    async def fake_transcribe(payload, extension):
        seen["payload"] = payload
        seen["extension"] = extension
        return {
            "text": "A reviewable transcript.",
            "languages": ["en"],
            "provider": "openai",
            "model": "gpt-transcribe",
        }

    monkeypatch.setattr(calliope.speech_settings, "transcribe", fake_transcribe)
    assert calliope.register_calliope_routes(
        MCP(), lambda: None, "", lambda _slug: ""
    ) is True
    handler = routes[("/api/calliope/transcriptions", ("POST",))]

    class Upload:
        closed = False

        async def read(self, size):
            assert size > len(audio)
            return audio

        async def close(self):
            self.closed = True

    class Request:
        def __init__(self, session):
            self.session = session
            self.headers = {"content-length": str(len(audio) + 256)}
            self.upload = Upload()

        async def form(self):
            return {"file": self.upload, "surface": "chat"}

    audio = b"\x1aE\xdf\xa3" + b"\0" * 80
    unauthenticated = asyncio.run(handler(Request(None)))
    assert unauthenticated.status_code == 401

    request = Request({"identity": "Pilot@Example.com", "mapped": True})
    response = asyncio.run(handler(request))
    body = json.loads(response.body)
    assert response.status_code == 200
    assert body["text"] == "A reviewable transcript."
    assert body["retained_audio"] is False
    assert seen == {
        "payload": audio,
        "extension": "webm",
    }
    assert request.upload.closed is True


def test_realtime_route_is_owner_gated_and_relays_only_valid_audio_sdp(monkeypatch, tmp_path):
    routes = {}

    class MCP:
        @staticmethod
        def custom_route(path, methods):
            def register(handler):
                routes[(path, tuple(methods))] = handler
                return handler
            return register

    fake_auth = types.SimpleNamespace(
        read_session_full=lambda request: getattr(request, "session", None)
    )
    monkeypatch.setitem(sys.modules, "auth", fake_auth)
    monkeypatch.setattr(calliope, "ensure_tables", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_docs, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope.hermes_config, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope.provider_settings, "ensure", lambda _factory: None)
    monkeypatch.setattr(calliope, "_start_session_synopsis_worker", lambda _factory: None)
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "hermes-key")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_DIR", str(tmp_path))
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_KEY", "speech-key")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_REALTIME_MODEL", "gpt-live-transcribe")
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_STT_KEYWORDS", "")

    seen = {}

    async def fake_create(method, path, body):
        seen.update(body)
        return {"sdp": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"}

    monkeypatch.setattr(calliope.speech_settings, "control", fake_create)
    assert calliope.register_calliope_routes(
        MCP(), lambda: None, "", lambda _slug: ""
    ) is True
    handler = routes[("/api/calliope/realtime-transcription", ("POST",))]

    class Request:
        def __init__(self, session, sdp):
            self.session = session
            self.sdp = sdp
            self.headers = {
                "content-type": "multipart/form-data; boundary=test",
                "content-length": str(len(sdp) + 256),
            }

        async def form(self):
            return {
                "sdp": self.sdp,
                "surface": "daily_note",
                "session_id": "73ea6745-ee4f-48eb-bc8c-350fb99d096a",
            }

    offer = "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    unauthenticated = asyncio.run(handler(Request(None, offer)))
    assert unauthenticated.status_code == 401

    invalid = asyncio.run(handler(Request(
        {"identity": "Pilot@Example.com", "mapped": True},
        "v=0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n",
    )))
    assert invalid.status_code == 400
    assert json.loads(invalid.body)["error"]["code"] == "BAD_REALTIME_OFFER"

    response = asyncio.run(handler(Request(
        {"identity": "Pilot@Example.com", "mapped": True}, offer
    )))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/sdp")
    assert response.headers["cache-control"] == "no-store"
    assert response.body.decode() == "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    assert seen == {
        "sdp": offer,
        "keywords": [],
        "languages": [],
    }


def test_speech_ui_is_review_first_and_available_in_chat_and_daily_notes():
    backend = (HERE / "calliope.py").read_text(encoding="utf-8")
    page = (HERE / "calliope" / "index.html").read_text(encoding="utf-8")
    script = (HERE / "calliope" / "calliope.js").read_text(encoding="utf-8")
    script += (HERE / "calliope" / "dictation.js").read_text()
    editor = (HERE / "calliope-editor" / "editor.js").read_text(encoding="utf-8")
    css = (HERE / "calliope" / "calliope.css").read_text(encoding="utf-8")

    assert '@mcp.custom_route("/api/calliope/transcriptions", methods=["POST"])' in backend
    assert '"/api/calliope/realtime-transcription", methods=["POST"]' in backend
    assert '"retained_audio": False' in backend
    assert 'data-speech-record="chat"' in page
    assert 'data-speech-record="daily_note"' in script
    assert 'data-speech-preview="chat"' in page
    assert 'data-speech-preview="daily_note"' in script
    assert "navigator.mediaDevices.getUserMedia" in script
    assert "new MediaRecorder" in script
    assert "new RTCPeerConnection" in script
    assert 'peer.createDataChannel("oai-events")' in script
    assert 'authenticatedFetch("/api/calliope/realtime-transcription"' in script
    assert "conversation.item.input_audio_transcription.delta" in script
    assert "conversation.item.input_audio_transcription.completed" in script
    assert 'type: "input_audio_buffer.commit"' in script
    assert 'authenticatedFetch("/api/calliope/transcriptions"' in script
    assert "composerInsertText(transcript, target.selection)" in script
    assert 'els.input.setRangeText(insert, from, to, "end")' in script
    assert "Transcript inserted · review it before sending" in script
    assert "getSelection" in editor
    assert "insertText(value" in editor
    assert ".speech-action.recording" in css
    assert ".speech-live-preview" in css
    live_handler = script.split("function handleRealtimeSpeechEvent", 1)[1].split(
        "function waitForRealtimeChannel", 1
    )[0]
    assert "insertSpeechTranscript" not in live_handler
    assert "sendTurn();" not in script.split("async function transcribeSpeechRecording", 1)[1].split(
        "async function finishSpeechRecording", 1
    )[0]


def test_recording_window_defaults_to_ten_minutes_and_respects_overrides(monkeypatch):
    monkeypatch.setenv("WAREHOUSE_HERMES_URL", "http://hermes:8642")
    monkeypatch.setenv("WAREHOUSE_HERMES_API_KEY", "hermes-key")
    monkeypatch.delenv("WAREHOUSE_CALLIOPE_MAX_AUDIO_SECONDS", raising=False)
    assert calliope.CalliopeConfig.from_env().max_audio_seconds == 600
    monkeypatch.setenv("WAREHOUSE_CALLIOPE_MAX_AUDIO_SECONDS", "240")
    assert calliope.CalliopeConfig.from_env().max_audio_seconds == 240
