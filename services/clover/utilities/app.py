"""CPU language ID and the existing SQL audio contract over hosted transcription."""
import asyncio
import base64
import binascii
from contextlib import asynccontextmanager
import io
import math
import os
import wave

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from lingua import LanguageDetectorBuilder
from pydantic import BaseModel, ConfigDict, Field, JsonValue

LANGUAGE_CONTRACT = "clover-language-v1/lingua-2.1.1-low"
TRANSCRIBE_CONTRACT = "clover-transcribe-v1"
TRANSCRIBE_MODEL = os.getenv("CLOVER_TRANSCRIBE_MODEL", "openai/gpt-transcribe")
AUDIO_FETCH = os.getenv("CLOVER_AUDIO_FETCH_URL", "http://clover-web:8091/internal/audio")
ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
MAX_AUDIO = 16 * 1024 * 1024


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: list[JsonValue] = Field(min_length=1, max_length=8)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 8_000_000:
                return await JSONResponse({"detail": "Request exceeds 8 MB"}, 413)(scope, receive, send)
            chunks.append(message)
            if not message.get("more_body", False):
                break

        async def replay():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, replay, send)


def prepare(name, item):
    if isinstance(item, str):
        item = {"text" if name == "language" else "audio": item}
    allowed = {"text"} if name == "language" else {"audio", "language"}
    if not isinstance(item, dict) or set(item) - allowed:
        raise ValueError("Unsupported input fields; transcription supports audio and optional language")
    field = "text" if name == "language" else "audio"
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be nonempty text")
    if name == "language":
        if len(value.encode()) > 32768 or not any(c.isalpha() for c in value):
            raise ValueError("Language input needs letters and must not exceed 32 KiB")
    else:
        if "language" in item and (not isinstance(item["language"], str) or not 2 <= len(item["language"]) <= 3 or not item["language"].isascii() or not item["language"].isalpha()):
            raise ValueError("language must be an ISO code")
        if value.startswith(("https://", "http://")):
            if len(value) > 8192:
                raise ValueError("Audio URL is too long")
        else:
            decode_audio(value)  # Validate every batch input before any provider call.
    return item


def decode_audio(value):
    if value.startswith("data:"):
        header, sep, value = value.partition(",")
        if not sep or not header.startswith("data:audio/") or not header.endswith(";base64"):
            raise ValueError("Expected a base64 audio data URI")
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Audio must be a public URL, base64 audio, or audio data URI") from None
    audio_kind(data)
    return data


def audio_kind(data):
    if not data or len(data) > MAX_AUDIO:
        raise ValueError("Audio must contain 1 byte to 16 MiB")
    # Sniff bytes, not URL suffix or remote Content-Type. Reject HTML/error pages
    # before spending on inference. No local paths are ever opened.
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav", "audio/wav"
    if data[:4] == b"fLaC":
        return "flac", "audio/flac"
    if data[:4] == b"OggS":
        return "ogg", "audio/ogg"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 255 and data[1] & 224 == 224):
        return "mp3", "audio/mpeg"
    if data[4:8] == b"ftyp":
        return "m4a", "audio/mp4"
    if data[:4] == b"\x1aE\xdf\xa3":
        return "webm", "audio/webm"
    raise ValueError("Unsupported audio bytes; use WAV, MP3, FLAC, Ogg, M4A or WebM")


def result_from_provider(raw, language, data, selected_model):
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        raise ValueError("Missing transcript")
    usage = raw.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("Invalid usage")
    usage = {k: v for k, v in usage.items() if k in ("cost", "input_tokens", "output_tokens", "prompt_tokens", "completion_tokens", "total_tokens", "seconds") and type(v) in (int, float) and math.isfinite(v) and v >= 0}
    usage.update(model=selected_model, contract=TRANSCRIBE_CONTRACT)
    duration = raw.get("duration")
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
        duration = None
    if duration is None and data[:4] == b"RIFF":
        try:
            with wave.open(io.BytesIO(data)) as wav:
                duration = wav.getnframes() / wav.getframerate()
        except (wave.Error, EOFError, ZeroDivisionError):
            pass
    return {"text": raw["text"], "chunks": [], "language": language,
            "duration_s": duration, "model": selected_model,
            "contract": TRANSCRIBE_CONTRACT, "usage": usage}


def provider_key():
    """Read an atomically replaced secret per request; never fall back from a bad file."""
    path = os.environ.get("OPENROUTER_API_KEY_FILE")
    if path is not None:
        try:
            with open(path, encoding="utf-8") as stream:
                value = stream.read(65537)
            if len(value) > 65536:
                return ""
        except (OSError, UnicodeError):
            return ""
    else:
        value = os.environ.get("OPENROUTER_API_KEY", "")
    value = value.strip()
    return value if "\n" not in value and "\r" not in value else ""


def create_app(*, api_key=None, transport=None, concurrency=2, queue_capacity=8, transcription_model=TRANSCRIBE_MODEL):
    if concurrency < 1 or queue_capacity < 0:
        raise ValueError("Invalid worker capacity")
    key = lambda: provider_key() if api_key is None else api_key

    @asynccontextmanager
    async def lifespan(app):
        app.state.active = 0
        app.state.slots = asyncio.Semaphore(concurrency)
        app.state.language_lock = asyncio.Lock()
        app.state.detector = LanguageDetectorBuilder.from_all_languages().with_low_accuracy_mode().build()
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(180, connect=5), trust_env=False, follow_redirects=False) as client:
            app.state.client = client
            yield

    app = FastAPI(title="Clover utilities", lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        return JSONResponse({"detail": "Invalid specialist request"}, 422)

    @app.get("/health")
    async def health():
        return {"status": "ready", "gpu": False, "language": LANGUAGE_CONTRACT,
                "transcribe": "configured" if key() else "missing_provider_key", "transcription_model": transcription_model}

    @app.get("/health/transcribe")
    async def transcription_health():
        credential = key()
        if not credential:
            return JSONResponse({"status": "missing_provider_key", "ok": False}, 503)
        try:
            # A configured string is not a usable transcription provider. These
            # authenticated metadata reads do not submit audio or run inference.
            base = ENDPOINT.rsplit('/audio/transcriptions', 1)[0]
            auth, catalogue = await asyncio.gather(*(
                app.state.client.get(base + path, headers={"Authorization": "Bearer " + credential}, timeout=3)
                for path in ('/key', '/models/user?output_modalities=all')
            ))
            auth.raise_for_status()
            catalogue.raise_for_status()
            account = auth.json()['data']
            remaining = account.get('limit_remaining')
            if remaining is not None and remaining <= 0:
                return JSONResponse({"status": "provider_limit_exhausted", "ok": False}, 503)
            model = next((m for m in catalogue.json()['data'] if m.get('id') == transcription_model), {})
            if 'transcription' not in model.get('architecture', {}).get('output_modalities', []):
                return JSONResponse({"status": "model_unavailable", "ok": False}, 503)
            return {"status": "ready", "ok": True, "model": transcription_model,
                    "provider_checked": True, "inference_verified": False}
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            return JSONResponse({"status": "provider_check_failed", "ok": False}, 503)

    async def language(item):
        # Bound CPU work and keep it off the event loop.
        async with app.state.language_lock:
            ranked = await asyncio.to_thread(app.state.detector.compute_language_confidence_values, item["text"])
        confidence = ranked[0].value if ranked else 0.0
        code = ranked[0].language.iso_code_639_1.name.lower() if confidence > 0 else "und"
        return {"language": code, "confidence": float(confidence), "model": "lingua-2.1.1-low",
                "contract": LANGUAGE_CONTRACT, "usage": {"cost": 0, "model": "lingua-2.1.1-low", "contract": LANGUAGE_CONTRACT}}

    async def transcribe(item):
        if not key():
            raise HTTPException(503, "Transcription provider is not configured")
        source = item["audio"]
        if source.startswith(("https://", "http://")):
            async with app.state.client.stream("POST", AUDIO_FETCH, json={"url": source}) as response:
                if response.status_code != 200:
                    raise HTTPException(422, "Audio URL could not be fetched; use a public HTTP(S) audio URL")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_AUDIO:
                        raise HTTPException(422, "Audio exceeds 16 MiB")
                data = bytes(data)
        else:
            data = decode_audio(source)
        ext, mime = audio_kind(data)
        fields = {"model": transcription_model, "response_format": "json"}
        if item.get("language"):
            fields["language"] = item["language"]
        async with app.state.client.stream("POST", ENDPOINT,
                headers={"Authorization": "Bearer " + key(), "X-Title": "Calliope Clover", "HTTP-Referer": "https://rvbbit.ai"},
                data=fields, files={"file": ("audio." + ext, data, mime)}) as response:
            if response.status_code == 429:
                raise HTTPException(429, "Transcription provider is busy")
            if not response.is_success:
                raise HTTPException(502, "Transcription provider rejected the request")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    raise HTTPException(502, "Transcription response exceeds limit")
            import json
            try:
                return result_from_provider(json.loads(body), item.get("language"), data, transcription_model)
            except (ValueError, TypeError):
                raise HTTPException(502, "Transcription provider returned an invalid response") from None

    @app.post("/specialists/{name}/predict")
    async def predict(name: str, batch: Batch):
        if name not in ("language", "transcribe"):
            raise HTTPException(404, "Unknown specialist")
        try:
            inputs = [prepare(name, item) for item in batch.inputs]
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        if app.state.active + len(inputs) > concurrency + queue_capacity:
            raise HTTPException(429, "Utility capacity is busy")
        app.state.active += len(inputs)
        async def run(item):
            async with app.state.slots:
                try:
                    return await (language(item) if name == "language" else transcribe(item))
                except ValueError as error:
                    raise HTTPException(422, str(error)) from None
                except httpx.TimeoutException:
                    raise HTTPException(504, "Utility upstream timed out") from None
                except httpx.HTTPError:
                    raise HTTPException(502, "Utility upstream is unavailable") from None
        try:
            outputs = await asyncio.gather(*(run(item) for item in inputs), return_exceptions=True)
            for out in outputs:
                if isinstance(out, BaseException):
                    raise out
            return {"outputs": outputs}
        finally:
            app.state.active -= len(inputs)

    return app


app = create_app()
