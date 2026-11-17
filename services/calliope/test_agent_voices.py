import asyncio
import io
import base64
import json
from types import SimpleNamespace
import pytest
import hermes_profiles
from test_calliope_voice import calliope
from test_team import load_function
from starlette.responses import Response, JSONResponse, StreamingResponse


def test_profile_voice_fallback_and_revision_metadata(monkeypatch):
    monkeypatch.setattr(hermes_profiles, 'profiles', lambda: {'profiles': [
        {'key':'default','voice_id':'40c20f2514ff4869a09ffcc840127f66'},
        {'key':'expert-profile','voice_id':'Expert-Voice_2'},
        {'key':'unassigned','voice_id':None}]})
    assert hermes_profiles.agent_voice('expert-profile') == 'Expert-Voice_2'
    assert hermes_profiles.agent_voice('unassigned') == hermes_profiles.agent_voice(None)
    assert hermes_profiles.clean_voice_id('https://bad/voice') == ''
    assert calliope._clean_voice_override('40c20f2514ff4869a09ffcc840127f66') == '40c20f2514ff4869a09ffcc840127f66'


def test_session_voice_uses_notebook_profile_for_anyone_in_the_room(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self,*_): pass
        def execute(self, query, args):
            assert 's.specialist_key' in query and 'rvbbit.calliope_session_can_view(s.id,%s,true)' in query
            assert args[1] == 'owner'
            return self
        def fetchone(self): return {'profile_name':'expert-profile'}
    monkeypatch.setattr(hermes_profiles,'agent_voice',lambda key:'expert-voice' if key=='expert-profile' else 'wrong')
    assert calliope._session_profile_voice(Connection,'owner','aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa') == 'expert-voice'


def test_voice_precedence_and_provider_capability():
    config=SimpleNamespace(voice_id='legacy')
    profile={'voice':'provider-default','voice_override':True}
    assert calliope._effective_tts_voice(config,profile,'character','tuning') == 'tuning'
    assert calliope._effective_tts_voice(config,profile,'character') == 'character'
    assert calliope._effective_tts_voice(config,profile) == 'provider-default'
    assert calliope._effective_tts_voice(config,{'voice':'fixed'},'character') == 'fixed'


@pytest.mark.parametrize('preview_text', [None, '[confident] I am Athena. I organize your business metrics.'])
@pytest.mark.parametrize('audio_payload', [b'\x00\x00' * 10, b'\x00', b''])
@pytest.mark.parametrize('authenticated,key,status',[(False,'expert',401),(True,'missing',404),(True,'expert',200)])
def test_preview_uses_character_voice_and_closes_provider(authenticated,key,status,audio_payload,preview_text):
    opened=[]; closed=[]
    async def roster(): return [{'key':'calliope','name':'Calliope','voice_id':'default','primary':True}, {'key':'expert','name':'Athena','voice_id':'character','voice_preview_text':preview_text}]
    class Upstream:
        async def aclose(self): closed.append(True)
    async def open_stream(config,render):
        opened.append(render)
        return Upstream(),Upstream()
    async def frames(config,upstream): yield audio_payload,None
    namespace=dict(asyncio=asyncio,json=json,conn_factory=None,
        _clean_voice_personality=calliope._clean_voice_personality,
        _agent_speech_persona=lambda *args:'persona',
        _prepare_agent_voice_preview=lambda factory,config,owner,agent,personality:agent.get('voice_preview_text') or "Hello, I'm Athena. I'm ready to get to work.",
        api_owner=lambda _:('owner',None) if authenticated else (None,JSONResponse({},401)),
        config=SimpleNamespace(voice_enabled=True,voice_id='legacy',voice_fast_model='model',voice_expressive_model='model',voice_prepare_timeout_seconds=10,voice_sample_rate=24000,max_voice_audio_bytes=100000),
        configured_chat_agents=roster,json_response=lambda payload,status:JSONResponse(payload,status),Response=Response,StreamingResponse=StreamingResponse,
        _VOICE_STREAM_PROTOCOL=calliope._VOICE_STREAM_PROTOCOL,_voice_stream_line=calliope._voice_stream_line,_voice_stream_error=calliope._voice_stream_error,
        hermes_profiles=hermes_profiles,VoiceProviderError=calliope.VoiceProviderError,
        _voice_synthesis_profile=lambda *args:{'voice_override':True,'voice':'default','sample_rate':24000},
        _effective_tts_voice=calliope._effective_tts_voice,_open_voice_provider_stream=open_stream,_voice_provider_frames=frames)
    route=load_function('preview_team_voice',namespace)
    async def body(): return b'{}'
    async def run():
        response=await route(SimpleNamespace(path_params={'agent_key':key},body=body))
        chunks=[chunk async for chunk in response.body_iterator] if response.status_code==200 else []
        return response,chunks
    response,chunks=asyncio.run(run())
    assert response.status_code == status
    if status == 200:
        assert opened[0]['tts_voice']=='character'
        assert opened[0]['script'] == (preview_text or "Hello, I'm Athena. I'm ready to get to work.")
        frames=[json.loads(chunk) for chunk in chunks]
        if len(audio_payload) == 20:
            assert base64.b64decode(frames[0]['audio_base64']) == audio_payload
        else:
            assert frames[0]['type'] == 'error' and frames[0]['message']
        assert response.headers['x-audio-sample-rate']=='24000'
        assert response.headers['x-calliope-audio-protocol']=='timed-pcm-ndjson-v1'
        assert len(closed)==2 and response.headers['cache-control']=='no-store'
    else: assert not opened


def test_preview_text_is_bounded_and_preserves_emotion_tags():
    assert hermes_profiles.clean_voice_preview_text('[curious] Hello.\n[calm] I check evidence.') == '[curious] Hello. [calm] I check evidence.'
    assert hermes_profiles.clean_voice_preview_text('x' * 801) == ''
    assert hermes_profiles.clean_voice_preview_text({'bad': 'shape'}) == ''
