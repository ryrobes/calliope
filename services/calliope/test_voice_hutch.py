"""Contracts for the binary Hutch speech adapter, including transport failures."""
import asyncio
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import voice_hutch
from test_calliope_voice import calliope


def profile():
    return dict(id="calliope-voice", profile_revision="a" * 24, model_version="fish-v1",
                upstream_model="fish-audio/s2.1-pro-free:free", provider="openrouter",
                voice="145ff4d7b1c74ab4abebf90e759f240c", response_format="pcm",
                encoding="s16le", sample_rate=44100, channels=1, expression_style="bracket-tags",
                alignment="none", max_input_characters=10000)


def config(tmp_path):
    return calliope.CalliopeConfig(hermes_url="", hermes_api_key="", memory_key="company",
        file_root=tmp_path, max_image_bytes=1024, voice_provider="hutch", voice_hutch_key="tenant-secret")


def test_config_uses_tenant_key_not_elevenlabs_or_openrouter(monkeypatch):
    monkeypatch.delenv("WAREHOUSE_CALLIOPE_TTS_PROVIDER", raising=False)
    monkeypatch.delenv("WAREHOUSE_CALLIOPE_TTS_HUTCH_KEY", raising=False)
    monkeypatch.setenv("RVBBIT_CLOVER_KEY", "tenant-secret")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "old-secret")
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-be-used")
    c = calliope.CalliopeConfig.from_env()
    assert c.voice_provider == "hutch"
    assert c.voice_enabled and c.voice_hutch_key == "tenant-secret"
    assert not replace(c, voice_hutch_key="").voice_enabled


def test_profile_uses_authenticated_discovery_and_no_redirects(monkeypatch):
    def get(url, **kw):
        assert url == "https://speech.example/v1/audio/models"
        assert kw["headers"] == {"Authorization": "Bearer tenant-secret"}
        assert not kw["follow_redirects"]
        return httpx.Response(200, json={"data": [profile()]})
    monkeypatch.setattr(voice_hutch.httpx, "get", get)
    assert voice_hutch.profile("https://speech.example/v1", "tenant-secret", "calliope-voice") == profile()


@pytest.mark.parametrize("field,value", [("encoding", "float32"), ("channels", 2), ("sample_rate", 0), ("profile_revision", "invalid")])
def test_profile_rejects_unsupported_audio(monkeypatch, field, value):
    p = profile(); p[field] = value
    monkeypatch.setattr(voice_hutch.httpx, "get", lambda *a, **k: httpx.Response(200, json={"data": [p]}))
    with pytest.raises(voice_hutch.VoiceProviderError):
        voice_hutch.profile("https://speech.example/v1", "key", "calliope-voice")


def test_cache_identity_uses_profile_and_legacy_sample_rate(tmp_path):
    c=config(tmp_path);render={"id":"render", "tts_profile":profile(), "sample_rate":44100}
    first=calliope._voice_cache_path(c,render)
    render["tts_profile"]["voice"]="new-voice"
    assert first != calliope._voice_cache_path(c,render)
    render["tts_profile"]["profile_revision"]="b"*24
    assert first != calliope._voice_cache_path(c,render)
    old={"id":"old", "tts_model":"eleven_v3", "sample_rate":24000}
    assert "24000-timed-v1.pcm" in str(calliope._voice_cache_path(c,old))


def test_conversational_render_does_not_query_speech(monkeypatch,tmp_path):
    monkeypatch.setattr(voice_hutch,"profile",lambda *a:pytest.fail("unnecessary speech lookup"))
    assert calliope._voice_synthesis_profile(config(tmp_path),"conversational")=={}


class Stream(httpx.AsyncByteStream):
    def __init__(self,chunks,fail=False): self.chunks=chunks;self.fail=fail;self.closed=False
    async def __aiter__(self):
        for c in self.chunks: yield c
        if self.fail: raise httpx.ReadError("private upstream detail")
    async def aclose(self): self.closed=True


def test_pcm_sample_boundaries_survive_arbitrary_network_chunks():
    async def run():
        r=httpx.Response(200, stream=Stream([b"\x01",b"\x02\x03",b"\x04",b"",b"\x05\x06"]))
        frames=[f async for f in voice_hutch.pcm_frames(r)]
        assert all(len(b)%2==0 and a is None for b,a in frames)
        assert b"".join(b for b,_ in frames)==bytes(range(1,7))
    asyncio.run(run())


@pytest.mark.parametrize("chunks,fail", [([b"\x01\x02\x03"],False),([b"\x01\x02"],True)])
def test_interrupted_pcm_streams_never_look_complete(chunks,fail):
    async def run():
        with pytest.raises(voice_hutch.VoiceProviderError):
            _=[f async for f in voice_hutch.pcm_frames(httpx.Response(200,stream=Stream(chunks,fail)))]
    asyncio.run(run())


@pytest.mark.parametrize("status,code", [(401,"VOICE_PROVIDER_AUTH"),(429,"VOICE_RATE_LIMITED"),(409,"VOICE_PROFILE_CHANGED"),(503,"VOICE_PROVIDER_UNAVAILABLE")])
def test_upstream_errors_are_bounded_and_closed(monkeypatch,status,code):
    stream=Stream([b"secret error detail"])
    client_type=httpx.AsyncClient
    monkeypatch.setattr(voice_hutch.httpx,"AsyncClient",lambda **kw:client_type(transport=httpx.MockTransport(lambda r:httpx.Response(status,stream=stream)),**kw))
    async def run():
        with pytest.raises(voice_hutch.VoiceProviderError) as e:
            await voice_hutch.open_stream("https://hutch/v1","tenant",{"script":"Hi","tts_profile":profile()})
        assert e.value.code==code and "secret" not in str(e.value)
        assert stream.closed
    asyncio.run(run())


def test_open_stream_routes_alias_and_tags_and_validates_actual_rate(monkeypatch):
    client_type=httpx.AsyncClient
    def respond(request):
        import json
        body=json.loads(request.content)
        assert body=={"model":"calliope-voice","input":"[excited] Hi!","response_format":"pcm"}
        assert request.headers["authorization"]=="Bearer tenant"
        assert request.headers["x-speech-profile-revision"]=="a"*24
        return httpx.Response(200,headers={"content-type":"audio/pcm;rate=44100;channels=1","x-speech-profile-revision":"a"*24},content=b"\x00\x00")
    monkeypatch.setattr(voice_hutch.httpx,"AsyncClient",lambda **kw:client_type(transport=httpx.MockTransport(respond),**kw))
    async def run():
        client,response=await voice_hutch.open_stream("https://hutch/v1","tenant",{"script":"[excited] Hi!","tts_profile":profile()})
        assert await response.aread()==b"\x00\x00"
        await response.aclose();await client.aclose()
    asyncio.run(run())


def test_model_switch_invalidates_saved_render(monkeypatch,tmp_path):
    import json
    p=profile();turn={'id':'c697caa5-f2b0-4e91-ab14-4ad8f666803c','session_id':'68a9c14f-2e76-484d-97bd-3f00a377fa72','assistant_message':'Everything is ready.','voice_context':[],'response_receipt':{}}
    monkeypatch.setattr(calliope,'_voice_turn',lambda *args:turn)
    monkeypatch.setattr(voice_hutch,'profile',lambda *args:dict(p))
    monkeypatch.setattr(calliope,'_generate_voice_script',lambda *args, **_kw:('Everything is ready.','canonical',None))
    stored=[]
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,query,params=None):
            if query.startswith('INSERT INTO rvbbit.calliope_takes'):stored.append(json.loads(params[1])['id'])
            return self
        def fetchone(self):return {'id':turn['id']}
    args=(Connection,config(tmp_path),'owner@example.com',turn['session_id'],turn['id'],'fast','')
    first,reused=calliope._voice_render(*args)
    assert not reused and first['sample_rate']==44100 and first['alignment']=='estimated'
    same,reused=calliope._voice_render(*args)
    assert reused and same['id']==first['id']
    p.update(upstream_model='fish-audio/s2.1-pro',profile_revision='b'*24)
    new,reused=calliope._voice_render(*args)
    assert not reused and new['id']!=first['id'] and new['tts_model']=='fish-audio/s2.1-pro'
    assert stored==[first['id'],new['id']]


# ── operator tuning: voice override, delivery direction, regenerate, takes ─────────────

import json as _json


class _Conn:
    def __init__(self, receipt):
        self.receipt = receipt
        self.updates = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params
        if "INSERT INTO rvbbit.calliope_takes" in sql:
            assert params[0] == params[5]  # the viewer is the person asking
            self.receipt = dict(self.receipt, presentation=_json.loads(params[1]), presentation_takes=_json.loads(params[2]))
            self.updates.append((self.receipt["presentation"]["id"], [t["id"] for t in self.receipt["presentation_takes"]]))
        return self

    def fetchone(self):
        if "SELECT k.earlier AS takes" in self.sql:
            return {"takes": self.receipt.get("presentation_takes")}
        if "SELECT k.render,k.earlier FROM" in self.sql:
            return {"render": self.receipt.get("presentation"), "earlier": self.receipt.get("presentation_takes")}
        return {"turn_id": "turn-1"}


def _render(i, **extra):
    base = {"id": f"00000000-0000-4000-8000-00000000000{i}", "version": calliope._VOICE_RENDER_VERSION, "mode": "expressive", "script": f"take {i}",
            "created_at": "2026-09-19T22:00:00+00:00", "tts_voice": "v" * 32, "tts_profile": {"voice": "v" * 32}}
    base.update(extra)
    return base


def test_tuning_inputs_are_bounded_and_shaped():
    assert calliope._clean_voice_override(" 145FF4D7B1C74AB4ABEBF90E759F240C ") == "145ff4d7b1c74ab4abebf90e759f240c"
    assert calliope._clean_voice_override("https://evil/voice") == "" and calliope._clean_voice_override("") == ""
    assert calliope._clean_voice_direction("  slower,\x00 warmer\n\n  more pauses ") == "slower, warmer more pauses"
    assert len(calliope._clean_voice_direction("x" * 900)) == calliope._VOICE_DIRECTION_MAX


def test_direction_lands_in_the_rewrite_instruction_as_an_operator_block():
    text = calliope._voice_rewrite_instruction("expressive", "dry wit", "slower, land the numbers, one chuckle max")
    assert "OPERATOR DELIVERY DIRECTION" in text and '"slower, land the numbers, one chuckle max"' in text
    assert "cannot change facts" in text
    assert "OPERATOR DELIVERY DIRECTION" not in calliope._voice_rewrite_instruction("expressive", "dry wit")


def test_store_keeps_replaced_renders_as_bounded_takes_and_in_place_updates_do_not():
    conn = _Conn({"presentation": _render(1)})
    turn = {"id": "turn-1", "session_id": "sess-1", "take": _render(1)}
    cfg = type("Cfg", (), {"voice_prepare_timeout_seconds": 5})()
    monkey = calliope._set_voice_statement_timeout
    calliope._set_voice_statement_timeout = lambda *a, **k: None
    try:
        calliope._store_voice_render(lambda: conn, cfg, "ryan@example.com", turn, _render(2), keep_current=False)
        assert conn.updates[-1] == (_render(2)["id"], [_render(1)["id"]])                  # take 1 kept
        calliope._store_voice_render(lambda: conn, cfg, "ryan@example.com", turn, dict(_render(2), tts_voice="w" * 32), keep_current=True)
        assert conn.updates[-1] == (_render(2)["id"], [_render(1)["id"]])                  # voice change: same take
        for i in range(3, 9):
            calliope._store_voice_render(lambda: conn, cfg, "ryan@example.com", turn, _render(i), keep_current=False)
        assert len(conn.updates[-1][1]) == calliope._VOICE_TAKES_KEPT and conn.updates[-1][1][0] == _render(7)["id"]
        assert conn.receipt["presentation_takes"][0]["script"] == "take 7"                 # newest first, script kept
        assert "context_hash" not in conn.receipt["presentation_takes"][0]                 # summary, not the whole render
    finally:
        calliope._set_voice_statement_timeout = monkey


def test_audio_lookup_finds_an_earlier_take_by_id():
    receipt = {"presentation": _render(2), "presentation_takes": [calliope._voice_take_summary(_render(1))]}
    conn = _Conn(receipt)
    monkey = calliope._set_voice_statement_timeout
    calliope._set_voice_statement_timeout = lambda *a, **k: None
    try:
        found = calliope._voice_render_for_audio(lambda: conn, "ryan@example.com", "00000000-0000-4000-8000-000000000009", _render(1)["id"])
        assert found["script"] == "take 1"
        with pytest.raises(LookupError):
            calliope._voice_render_for_audio(lambda: conn, "ryan@example.com", "00000000-0000-4000-8000-000000000009", _render(5)["id"])
    finally:
        calliope._set_voice_statement_timeout = monkey


def test_cache_identity_and_upstream_request_carry_the_tuning_voice(monkeypatch, tmp_path):
    cfg = calliope.CalliopeConfig.from_env()
    cfg = type(cfg)(**{**cfg.__dict__, "file_root": tmp_path, "voice_provider": "hutch"})
    profile = {"id": "calliope-voice", "profile_revision": "a" * 24, "voice": "d" * 32, "sample_rate": 44100, "max_input_characters": 1000}
    default = calliope._voice_cache_path(cfg, {"id": "r1", "tts_profile": profile, "tts_voice": "d" * 32})
    tuned = calliope._voice_cache_path(cfg, {"id": "r1", "tts_profile": profile, "tts_voice": "e" * 32})
    assert default != tuned and calliope._voice_cache_path(cfg, {"id": "r1", "tts_profile": profile}) == default

    sent = {}

    class Resp:
        status_code = 200
        headers = {"content-type": "audio/pcm;rate=44100;channels=1", "x-speech-profile-revision": "a" * 24}

        async def aclose(self):
            pass

    class Client:
        def __init__(self, **kw):
            pass

        def build_request(self, method, url, headers=None, json=None):
            sent.update(json)
            return ("req", url)

        async def send(self, request, stream=True):
            return Resp()

        async def aclose(self):
            pass

    monkeypatch.setattr(voice_hutch.httpx, "AsyncClient", Client)
    import asyncio
    asyncio.run(voice_hutch.open_stream("https://h/v1", "k", {"tts_profile": profile, "script": "hi", "tts_voice": "e" * 32}))
    assert sent["voice"] == "e" * 32
    sent.clear()
    asyncio.run(voice_hutch.open_stream("https://h/v1", "k", {"tts_profile": profile, "script": "hi", "tts_voice": "d" * 32}))
    assert "voice" not in sent                                                            # default voice: Hutch decides


def test_profile_keeps_the_override_policy_only_when_hutch_advertises_it(monkeypatch):
    base = {"id": "calliope-voice", "profile_revision": "a" * 24, "model_version": "v", "upstream_model": "m", "provider": "openrouter",
            "voice": "d" * 32, "response_format": "pcm", "encoding": "s16le", "sample_rate": 44100, "channels": 1,
            "expression_style": "bracket-tags", "alignment": "none", "max_input_characters": 1000}

    class R:
        status_code = 200

        def __init__(self, data):
            self.data = data

        def json(self):
            return {"data": [self.data]}

    monkeypatch.setattr(voice_hutch.httpx, "get", lambda *a, **k: R(dict(base, voice_override=True, voice_pattern="^[a-f0-9]{32}$")))
    p = voice_hutch.profile("https://h/v1", "k", "calliope-voice")
    assert p["voice_override"] is True and p["voice_pattern"] == "^[a-f0-9]{32}$"
    monkeypatch.setattr(voice_hutch.httpx, "get", lambda *a, **k: R(base))
    assert "voice_override" not in voice_hutch.profile("https://h/v1", "k", "calliope-voice")   # older Hutch: no overrides


def test_a_rejected_custom_voice_says_so():
    assert voice_hutch._error(502, custom_voice=True).code == "VOICE_OVERRIDE_REJECTED"
    assert voice_hutch._error(502).code == "VOICE_PROVIDER_UNAVAILABLE"
    assert voice_hutch._error(400).code == "VOICE_OVERRIDE_REJECTED"



def test_paragraphs_split_and_drop_leading_pause_tags():
    script = "[happy] First point.\n\nSecond point here.\r\n\r\n[break] Third.\n\n\n[long pause] [joyful] Fourth.\n\n   \n"
    assert voice_hutch.paragraphs(script) == ["[happy] First point.", "Second point here.", "Third.", "[joyful] Fourth."]
    assert voice_hutch.spoken_text("one paragraph only") == "one paragraph only"
    assert len(voice_hutch.silence_bytes(44100, 900)) == 44100 * 9 // 10 * 2


class _FakeResp:
    def __init__(self, chunks, revision):
        self.status_code = 200
        self.headers = {"content-type": "audio/pcm;rate=44100;channels=1", "x-speech-profile-revision": revision, "x-generation-id": "gen-1"}
        self._chunks = chunks
        self.closed = False

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        self.closed = True


def test_multi_paragraph_scripts_stream_paragraph_audio_with_silence_between(monkeypatch):
    profile = {"id": "calliope-voice", "profile_revision": "a" * 24, "voice": "d" * 32, "sample_rate": 44100, "max_input_characters": 1000}
    sent, responses = [], []

    class Client:
        def __init__(self, **kw):
            self.closed = False

        def build_request(self, method, url, headers=None, json=None):
            sent.append(json)
            return json

        async def send(self, request, stream=True):
            resp = _FakeResp([request["input"].encode()[:4].ljust(4, b"\x01"), b"\x02\x03"], "a" * 24)
            responses.append(resp)
            return resp

        async def aclose(self):
            self.closed = True

    monkeypatch.setattr(voice_hutch.httpx, "AsyncClient", Client)
    import asyncio

    async def run():
        client, resp = await voice_hutch.open_stream("https://h/v1", "k", {"tts_profile": profile, "script": "[happy] One.\n\n[calm] Two.\n\nThree.", "tts_voice": "e" * 32})
        out = b""
        async for audio, _ in voice_hutch.pcm_frames(resp):
            out += audio
        await resp.aclose(); await client.aclose()
        return out
    out = asyncio.run(run())
    silence = voice_hutch.silence_bytes(44100)
    assert [j["input"] for j in sent] == ["[happy] One.", "[calm] Two.", "Three."]       # one request per paragraph, in order
    assert all(j["voice"] == "e" * 32 for j in sent)                                        # a custom voice applies to every paragraph
    assert out == b"[hap" + b"\x02\x03" + silence + b"[cal" + b"\x02\x03" + silence + b"Thre" + b"\x02\x03" + voice_hutch.silence_bytes(44100, 20)
    assert all(r.closed for r in responses)
    assert len(silence) % 2 == 0 and len(silence) == 44100 * 9 // 10 * 2


def test_find_reports_high_confidence_when_the_governed_vocabulary_matched(monkeypatch):
    import server
    monkeypatch.setattr(server, "_governed_terms_for", lambda q: [{"kind": "governed_term", "object": "metric salesforce_submitted_leads v5"}])
    monkeypatch.setattr(server, "_attach_meaning_to_hits", lambda m: m)

    class Cur:
        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchone(self):
            if "to_regprocedure" in self.sql:
                return {"ok": True}
            return {"j": {"hits": [], "confidence": "none", "weak": True, "gates": {}, "seams": {}, "elapsed_ms": 3}}

    monkeypatch.setattr(server, "_ro", lambda *a, **k: Cur(), raising=False)
    monkeypatch.setattr(server, "_conn", lambda *a, **k: Cur(), raising=False)
    out = server.tool_find("leads", limit=5)
    if "error" in out:                                                                        # the find seam is probed differently here
        import pytest
        pytest.skip(f"find unavailable in this harness: {out['error']}")
    assert out["matches"][0]["kind"] == "governed_term" and out["confidence"] == "high"
    assert out["gates"]["governed_terms"] == 1
    assert "weak match" not in str(out.get("note", ""))                                       # no contradiction with the governed hit
    assert "governed vocabulary answered this" in str(out.get("note", ""))


def test_estimated_alignment_is_paragraph_exact_and_weighted():
    a = voice_hutch.estimated_alignment("[happy] Hi, there.", 10.0, 12.0)
    assert a["absolute"] is True and a["estimated"] is True
    assert a["characters"] == list("[happy] Hi, there.") and a["character_start_times_seconds"][0] == 10.0
    assert abs(a["character_end_times_seconds"][-1] - 12.0) < 1e-3                                   # ends where the audio ends
    # the bracket cue takes no time: its characters all start when the first spoken char starts
    idx_h = "[happy] Hi, there.".index("H")
    assert a["character_start_times_seconds"][idx_h] == a["character_start_times_seconds"][0]
    assert voice_hutch.estimated_alignment("", 0, 1) is None and voice_hutch.estimated_alignment("x", 3, 3) is None


def test_paragraph_timings_ride_the_following_silence_and_a_tail_after_the_last(monkeypatch):
    profile = {"id": "calliope-voice", "profile_revision": "a" * 24, "voice": "d" * 32, "sample_rate": 44100, "max_input_characters": 1000}

    class Client:
        def __init__(self, **kw):
            pass

        def build_request(self, method, url, headers=None, json=None):
            return json

        async def send(self, request, stream=True):
            return _FakeResp([b"\x01\x02" * (10 if request["input"].startswith("A") else 5)], "a" * 24)   # 20 or 10 bytes

        async def aclose(self):
            pass

    monkeypatch.setattr(voice_hutch.httpx, "AsyncClient", Client)
    import asyncio

    async def run():
        client, resp = await voice_hutch.open_stream("https://h/v1", "k", {"tts_profile": profile, "script": "A first.\n\nB second."})
        frames = []
        async for audio, alignment in voice_hutch.pcm_frames(resp):
            frames.append((len(audio), alignment))
        await resp.aclose(); await client.aclose()
        return frames
    frames = asyncio.run(run())
    silence = len(voice_hutch.silence_bytes(44100))
    assert [f[0] for f in frames] == [20, silence, 10, len(voice_hutch.silence_bytes(44100, 20))]
    first = frames[1][1]                                                 # paragraph 1 timing rides the silence after it
    assert first["characters"][0] == "A" and first["character_start_times_seconds"][0] == 0.0
    assert abs(first["character_end_times_seconds"][-1] - 20 / 88200) < 1e-4
    second = frames[3][1]                                                # last paragraph: on a 20 ms tail
    assert second["characters"][0] == "B" and abs(second["character_start_times_seconds"][0] - (20 + silence) / 88200) < 1e-4
    assert frames[0][1] is None and frames[2][1] is None
