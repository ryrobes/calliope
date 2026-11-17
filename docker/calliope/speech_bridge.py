"""Calliope dictation transport, with Hermes-owned credentials and settings.

The default Hermes profile owns this appliance capability. This is not an
agent turn. Its small settings file is deliberately separate from agent YAML
so changing dictation never overwrites models or needs a gateway restart.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path
import tempfile
import threading
import time

import httpx

_LOCK = threading.RLock()
_MODELS = ("gpt-live-transcribe",)
_DEFAULT = {"provider": "openai-api", "model": _MODELS[0]}
MAX_AUDIO_BYTES = 25 * 1024 * 1024


def _path():
    return Path(os.environ.get("HERMES_HOME", "/opt/data")) / "calliope-speech.json"


def _read():
    path = _path()
    return json.loads(path.read_text()) if path.exists() else ({"provider":"clover", "model":"calliope-stt"} if os.environ.get("RVBBIT_CLOVER_KEY") else dict(_DEFAULT))


def _key():
    if _read()["provider"] == "clover":
        return os.environ.get("RVBBIT_CLOVER_KEY", "")
    # Use Hermes' native profile-aware resolver, including hermes auth add.
    # Never use the Codex OAuth credential or send the key to Calliope.
    from tools.tool_backend_helpers import resolve_openai_audio_api_key
    return resolve_openai_audio_api_key()


def settings():
    with _LOCK:
        value = _read()
        revision = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    available = bool(_key())
    enabled = value["provider"] in ("openai-api", "clover") and available
    return {**value, "revision": revision, "enabled": enabled,
            "credential_available": available, "models": ["calliope-stt", *_MODELS],
            "batch_model": "calliope-stt" if value["provider"] == "clover" else "gpt-transcribe", "retains_audio": False,
            "realtime": {"enabled": enabled and value["provider"] == "openai-api", "model": value["model"],
                         "transport": "webrtc", "batch_fallback": True}}


def save(body):
    provider, model = body.get("provider"), body.get("model")
    if provider not in ("off", "openai-api", "clover") or model not in (*_MODELS,"calliope-stt"):
        raise ValueError("Choose Clover, OpenAI API or Off and a supported live transcription model.")
    if provider == "clover": model = "calliope-stt"
    elif provider == "openai-api": model = _MODELS[0]
    with _LOCK:
        current = settings()
        if body.get("revision") != current["revision"]:
            raise ValueError("Dictation settings changed. Refresh and review your selection.")
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".speech-", dir=path.parent)
        try:
            with os.fdopen(fd, "w") as output:
                json.dump({"provider": provider, "model": model}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return settings()


def _post(path, **kwargs):
    key = _key()
    if _read()["provider"] not in ("openai-api", "clover") or not key:
        raise ValueError("Connect the selected speech provider credential and enable dictation.")
    managed = _read()["provider"] == "clover"
    base = os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1").rstrip('/') if managed else "https://api.openai.com/v1"
    started = time.monotonic()
    try:
        with httpx.Client(timeout=httpx.Timeout(60, connect=10), follow_redirects=False) as client:
            response = client.post(base + "/" + path,
                                   headers={"Authorization": "Bearer " + key}, **kwargs)
    except httpx.HTTPError:
        raise ValueError("The speech provider could not be reached.") from None
    try:
        from gateway.calliope_media_ledger import record
        is_live = path == "realtime/calls"
        payload = {} if is_live or response.status_code >= 400 else response.json()
        record(operation="stt.live_setup" if is_live else "stt.transcribe",
               model=("calliope-stt-live" if is_live else "calliope-stt") if managed else (_read()["model"] if is_live else "gpt-transcribe"),
               base_url=base, headers=dict(response.headers), usage=payload.get("usage"),
               output_bytes=len(response.content), latency_ms=(time.monotonic()-started)*1000,
               status="error" if response.status_code >= 400 else "ok")
    except (ImportError, AttributeError):
        pass
    if response.status_code >= 400:
        # Provider bodies can contain request data: don't echo them to clients/logs.
        raise ValueError(f"The speech provider rejected the request (HTTP {response.status_code}).")
    return response


def realtime(body):
    if _read()["provider"] == "clover":
        raise ValueError("Clover dictation transcribes when recording stops.")
    sdp = body.get("sdp", "")
    if (not isinstance(sdp, str) or len(sdp.encode()) > 128 * 1024
            or not sdp.startswith("v=0") or "m=audio" not in sdp
            or "m=video" in sdp or "\x00" in sdp):
        raise ValueError("Invalid audio connection offer.")
    # Never accept a caller-supplied provider URL, key, session type or model.
    hints = body.get("keywords", [])
    if not isinstance(hints, list) or len(hints) > 64:
        raise ValueError("Invalid transcription vocabulary.")
    hints = [str(item)[:100] for item in hints if not any(c in str(item) for c in "<>\r\n")]
    transcription = {"model": _read()["model"], "prompt": "Transcribe the user's dictation accurately. Preserve names, numbers, and technical terms."}
    if hints:
        transcription["keywords"] = hints
    languages = body.get("languages", [])
    if not isinstance(languages, list) or len(languages) > 8 or any(
            not isinstance(code, str) or not re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2})?", code) for code in languages):
        raise ValueError("Invalid transcription languages.")
    if languages:
        transcription["languages"] = languages
    session = {"type": "transcription", "audio": {"input": {
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": transcription, "turn_detection": None}}}
    response = _post("realtime/calls", files={
        "sdp": (None, sdp, "application/sdp"),
        "session": (None, json.dumps(session), "application/json")})
    if not response.text.startswith("v=0") or "m=audio" not in response.text:
        raise ValueError("The speech provider returned an invalid connection answer.")
    return {"sdp": response.text}


def transcribe(body):
    try:
        audio = base64.b64decode(body.get("audio", ""), validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid audio recording.") from None
    if not 32 <= len(audio) <= MAX_AUDIO_BYTES:
        raise ValueError("Recording is empty or too large.")
    formats = {"webm": "audio/webm", "m4a": "audio/mp4", "mp4": "audio/mp4", "wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg"}
    extension = body.get("extension")
    if extension not in formats:
        raise ValueError("Unsupported recording format.")
    model = "calliope-stt" if _read()["provider"] == "clover" else "gpt-transcribe"
    response = _post("audio/transcriptions", data={"model": model},
                     files={"file": ("dictation." + extension, audio, formats[extension])})
    text = str(response.json().get("text", "")).strip()
    if not text:
        raise ValueError("No speech was detected. Check that your microphone is on.")
    return {"text": text, "model": model, "provider": _read()["provider"], "retained_audio": False}
