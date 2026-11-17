"""Hutch speech adapter. Only the tenant credential reaches this service."""
from __future__ import annotations

import re
from typing import AsyncIterator

import httpx


class VoiceProviderError(RuntimeError):
    """A bounded, user-safe failure from the configured voice provider."""

    def __init__(self, message: str, code: str = "VOICE_FAILED"):
        super().__init__(message)
        self.code = code


def _error(status: int, custom_voice: bool = False) -> VoiceProviderError:
    if status == 409:
        return VoiceProviderError("Voice settings changed. Replay to prepare the new voice.", "VOICE_PROFILE_CHANGED")
    if status == 400:
        return VoiceProviderError("The speech service refused that voice. Check the voice id or clear it.", "VOICE_OVERRIDE_REJECTED")
    if status in {401, 403}:
        return VoiceProviderError("The speech service rejected the configured credentials.", "VOICE_PROVIDER_AUTH")
    if status == 429:
        return VoiceProviderError("Spoken responses are temporarily at capacity. Try again shortly.", "VOICE_RATE_LIMITED")
    if custom_voice and status in {502, 503}:
        return VoiceProviderError("The speech provider could not synthesize with that custom voice id. Check it, or clear it to use the default voice.", "VOICE_OVERRIDE_REJECTED")
    return VoiceProviderError("The speech service is unavailable. Try again in a moment.", "VOICE_PROVIDER_UNAVAILABLE")


def profile(base_url: str, key: str, alias: str) -> dict:
    """Resolve on each new render, so operator switches take effect immediately."""
    try:
        response = httpx.get(base_url.rstrip("/") + "/audio/models",
                             headers={"Authorization": "Bearer " + key},
                             timeout=12, follow_redirects=False)
        if response.status_code != 200:
            raise _error(response.status_code)
        value = next((p for p in response.json()["data"] if p.get("id") == alias), None)
        if not value:
            raise VoiceProviderError("The configured speech model is unavailable.", "VOICE_UNAVAILABLE")
        if (value.get("encoding") != "s16le" or value.get("channels") != 1
                or value.get("response_format") != "pcm"
                or not 8000 <= int(value.get("sample_rate", 0)) <= 192000
                or not re.fullmatch(r"[a-f0-9]{24}", str(value.get("profile_revision", "")))):
            raise VoiceProviderError("The speech service returned an unsupported audio profile.", "VOICE_PROVIDER_STREAM_INVALID")
        profile = {k: value[k] for k in (
            "id", "profile_revision", "model_version", "upstream_model", "provider", "voice",
            "response_format", "encoding", "sample_rate", "channels", "expression_style",
            "alignment", "max_input_characters")}
        # Operator tuning policy, advertised by newer Hutch builds; absent means "no overrides".
        if value.get("voice_override") is True:
            profile["voice_override"] = True
            profile["voice_pattern"] = str(value.get("voice_pattern") or "")[:200]
        return profile
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
        raise VoiceProviderError("The speech service could not be reached or returned an invalid profile.", "VOICE_PROVIDER_UNAVAILABLE") from exc


_PARAGRAPH_GAP = re.compile(r"\n[ \t]*\n+")
_PAUSE_TAG = re.compile(r"^\s*\[(?:break|long-break|long pause|short pause|pause)\]\s*", re.IGNORECASE)
PARAGRAPH_SILENCE_MS = 900


def paragraphs(script: str) -> list[str]:
    """A blank line is a paragraph to the eye and nothing to the speech model, which reads
    straight through it (measured: a pause tag buys ~0.2 s). So paragraphs are spoken one
    request at a time and real silence goes between them; a pause tag a writer put at the
    top of a paragraph is dropped, the silence does its job."""
    parts = [part.strip() for part in _PARAGRAPH_GAP.split(str(script or "").replace("\r\n", "\n")) if part.strip()]
    return [_PAUSE_TAG.sub("", part, count=1).strip() or part for part in parts]


def spoken_text(script: str) -> str:
    return "\n\n".join(paragraphs(script))


def silence_bytes(sample_rate: int, ms: int = PARAGRAPH_SILENCE_MS) -> bytes:
    frames = max(0, int(int(sample_rate) * ms / 1000))
    return b"\x00\x00" * frames


class ParagraphBoundary:
    """Yielded by ParagraphStream between paragraphs: everything needed to time the
    paragraph that just finished. Exact at the edges (we counted its bytes), estimated
    inside (characters spread by weight)."""
    __slots__ = ("index", "text", "start_bytes", "end_bytes", "last")

    def __init__(self, index: int, text: str, start_bytes: int, end_bytes: int, last: bool):
        self.index, self.text, self.start_bytes, self.end_bytes, self.last = index, text, start_bytes, end_bytes, last


def _character_weight(ch: str, in_tag: bool) -> float:
    if in_tag:
        return 0.0                                      # a bracket cue is not spoken
    if ch.isalnum():
        return 1.0
    if ch in ".!?…":
        return 1.6
    if ch in ",;:—–-":
        return 0.9
    if ch.isspace():
        return 0.35
    return 0.15


def estimated_alignment(text: str, start_seconds: float, end_seconds: float) -> dict | None:
    """Character timings for one paragraph, absolute in stream seconds: bracket cues take no
    time, punctuation takes a breath, letters share the rest. The same shape as a real
    character alignment so the browser's karaoke absorbs it unchanged; `absolute` tells it
    these are stream offsets, and `estimated` says they are not the model's own."""
    text = str(text or "")
    span = max(0.0, float(end_seconds) - float(start_seconds))
    if not text or span <= 0:
        return None
    weights, in_tag, after_tag = [], False, False
    for ch in text:
        if ch == "[":
            in_tag = True
        if after_tag and ch.isspace():
            weights.append(0.0)                          # the space after a cue is not a breath
        else:
            weights.append(_character_weight(ch, in_tag))
            after_tag = False
        if ch == "]":
            in_tag, after_tag = False, True
    total = sum(weights) or 1.0
    unit = span / total
    starts, ends, cursor = [], [], float(start_seconds)
    for w in weights:
        starts.append(round(cursor, 4))
        cursor += w * unit
        ends.append(round(cursor, 4))
    return {"characters": list(text), "character_start_times_seconds": starts, "character_end_times_seconds": ends,
            "absolute": True, "estimated": True}


class ParagraphStream:
    """One response-like object over several upstream requests: paragraph audio in order,
    with silence between paragraphs. The next paragraph is requested while the current
    one streams, so the gap is silence and not a round trip. Looks like the single
    response to its readers: status_code, headers (from the first paragraph), aiter_bytes,
    aclose."""

    def __init__(self, base_url: str, key: str, profile: dict, parts: list[str], voice: str = "", silence_ms: int = PARAGRAPH_SILENCE_MS):
        self._base_url, self._key, self._profile, self._parts, self._voice = base_url, key, profile, parts, voice
        self._silence = silence_bytes(int(profile["sample_rate"]), silence_ms)
        self.sample_rate = int(profile["sample_rate"])
        self._current: tuple[httpx.AsyncClient, httpx.Response] | None = None
        self._next = None
        self.status_code = 200
        self.headers: dict = {}

    async def start(self) -> None:
        self._current = await _open_one(self._base_url, self._key, self._profile, self._parts[0], self._voice)
        self.headers = dict(self._current[1].headers)

    async def aiter_bytes(self):
        import asyncio
        position = 0
        for index in range(len(self._parts)):
            if self._current is None:
                self._current = await self._next if self._next is not None else await _open_one(self._base_url, self._key, self._profile, self._parts[index], self._voice)
                self._next = None
            client, response = self._current
            if index + 1 < len(self._parts):
                self._next = asyncio.ensure_future(_open_one(self._base_url, self._key, self._profile, self._parts[index + 1], self._voice))
            started = position
            try:
                async for chunk in response.aiter_bytes():
                    position += len(chunk)
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()
                self._current = None
            last = index + 1 >= len(self._parts)
            yield ParagraphBoundary(index, self._parts[index], started, position, last)
            if not last:
                position += len(self._silence)
                yield self._silence

    async def aclose(self) -> None:
        if self._current is not None:
            client, response = self._current
            self._current = None
            await response.aclose()
            await client.aclose()
        if self._next is not None:
            pending, self._next = self._next, None
            pending.cancel()
            try:
                client, response = await pending
                await response.aclose()
                await client.aclose()
            except BaseException:
                pass


async def _open_one(base_url: str, key: str, p: dict, text: str, voice: str = "") -> tuple[httpx.AsyncClient, httpx.Response]:
    client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=12, read=60), follow_redirects=False)
    response = None
    try:
        body = {"model": p["id"], "input": text, "response_format": "pcm"}
        if voice and voice != str(p.get("voice") or ""):
            body["voice"] = voice                          # operator tuning; Hutch enforces its policy
        request = client.build_request("POST", base_url.rstrip("/") + "/audio/speech",
            headers={"Authorization": "Bearer " + key, "x-speech-profile-revision": p["profile_revision"]},
            json=body)
        response = await client.send(request, stream=True)
        if response.status_code != 200:
            raise _error(response.status_code, custom_voice="voice" in body)
        content_type = response.headers.get("content-type", "")
        parts = [part.strip().lower() for part in content_type.split(";")]
        params = dict(part.split("=", 1) for part in parts[1:] if "=" in part)
        if (parts[0] != "audio/pcm" or params.get("channels") != "1"
                or params.get("rate") != str(p["sample_rate"])
                or response.headers.get("x-speech-profile-revision") != p["profile_revision"]):
            raise VoiceProviderError("The speech service returned an unexpected audio format.", "VOICE_PROVIDER_STREAM_INVALID")
        return client, response
    except BaseException as exc:
        if response is not None:
            await response.aclose()
        await client.aclose()
        if isinstance(exc, httpx.HTTPError):
            raise VoiceProviderError("The speech service could not be reached.", "VOICE_PROVIDER_UNAVAILABLE") from exc
        raise


async def open_stream(base_url: str, key: str, render: dict):
    """(client, response) for the render's script. One paragraph: the upstream response as
    is. Several: a ParagraphStream, returned as both client and response (both close it)."""
    p = render.get("tts_profile") or {}
    if not p.get("profile_revision"):
        raise VoiceProviderError("Prepare this response again to use the new voice.", "VOICE_PROFILE_CHANGED")
    parts = paragraphs(render.get("script") or "")
    text = "\n\n".join(parts)
    if not parts:
        raise VoiceProviderError("There is nothing to speak in this response.", "VOICE_PROVIDER_STREAM_INVALID")
    if len(text) > p["max_input_characters"] or any(len(part) > p["max_input_characters"] for part in parts):
        raise VoiceProviderError("This response exceeds the speech provider's input limit. The complete answer is available on screen.", "VOICE_INPUT_TOO_LARGE")
    voice = str(render.get("tts_voice") or "").strip()
    if len(parts) == 1:
        return await _open_one(base_url, key, p, parts[0], voice)
    stream = ParagraphStream(base_url, key, p, parts, voice)
    await stream.start()
    return stream, stream


async def pcm_frames(response, sample_rate: int | None = None) -> AsyncIterator[tuple[bytes, dict | None]]:
    """HTTP boundaries need not align with 16-bit PCM samples. A ParagraphStream also
    reports paragraph boundaries; each becomes an estimated alignment frame riding on
    the silence that follows the paragraph (or a 20 ms tail after the last one)."""
    pending = b""
    carried: dict | None = None                      # a paragraph's timing rides the next audio frame
    rate = int(sample_rate or getattr(response, "sample_rate", 0) or 0)
    try:
        async for chunk in response.aiter_bytes():
            if isinstance(chunk, ParagraphBoundary):
                if pending:
                    raise VoiceProviderError("The speech stream ended with an incomplete audio sample.", "VOICE_PROVIDER_STREAM_INVALID")
                if rate:
                    alignment = estimated_alignment(chunk.text, chunk.start_bytes / (rate * 2), chunk.end_bytes / (rate * 2))
                    if alignment and chunk.last:
                        yield silence_bytes(rate, 20), alignment
                    elif alignment:
                        carried = alignment
                continue
            pending += chunk
            size = len(pending) - len(pending) % 2
            if size:
                yield pending[:size], carried
                carried = None
                pending = pending[size:]
        if pending:
            raise VoiceProviderError("The speech stream ended with an incomplete audio sample.", "VOICE_PROVIDER_STREAM_INVALID")
    except httpx.HTTPError as exc:
        raise VoiceProviderError("The speech stream was interrupted. Try replaying the answer.", "VOICE_PROVIDER_UNAVAILABLE") from exc
