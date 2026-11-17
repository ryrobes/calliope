"""Pinned native endpoint contract: retry-safe steering and terminal replay."""
import sys
sys.path.insert(0, "/opt/hermes")
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
from gateway.platforms import api_server_runs as runs

class Agent:
    def __init__(self): self.messages=[]
    def steer(self,text): self.messages.append(text);return True

async def check():
    agent=Agent();state={'status':'running'};writes=[]
    class Server:
        async def _read_json_body(self,request):return request,None
        def _set_run_status(self,rid,status,**fields):state.update(status=status,**fields);writes.append(dict(fields))
    server=Server()
    api=SimpleNamespace(_normalize_chat_content=lambda x:x,_openai_error=lambda msg,**kw:{'error':{'message':msg,**kw}})
    def load(*args,**kw):return 'run',state,agent,None,None
    async def send(text='new guidance'):
        response=await runs._handle_steer_run(server,{'request_id':'correction-1','input':text},_api_server=api)
        return response.status,json.loads(response.body)
    with patch.object(runs,'_load_owned_run',load),patch.object(runs,'_mark_run_event',lambda *a,**kw:None):
        assert (await send())[0]==200 and len(agent.messages)==1
        assert (await send())[1]['replayed'] and len(agent.messages)==1
        assert (await send('different'))[0]==409 and len(agent.messages)==1
        state['status']='completed'
        assert (await send())[1]['replayed'] and len(agent.messages)==1
        response=await runs._handle_steer_run(server,{'request_id':'other','input':'late'},_api_server=api)
        assert response.status==409
        assert writes[0]['steer_receipts']['correction-1']['sha256']
    assert runs.terminal_run_status({'completed':True,'pending_steer':'late'})[1]['pending_steer']=='late'

asyncio.run(check())
print('Native steering idempotency, conflict and completion-race contracts passed')

# Exercise the native non-interrupting boundary: two messages concatenate,
# become one user row after the tools, and are drained exactly once.
import logging
from agent.interrupt_control import InterruptControlMixin
from agent import agent_runtime_helpers as helpers
agent=InterruptControlMixin()
assert agent.steer('first correction') and agent.steer('second correction')
messages=[{'role':'assistant','tool_calls':[{'id':'tool-1'}]},
          {'role':'tool','tool_call_id':'tool-1','content':'done'}]
with patch.object(helpers,'_ra',lambda:SimpleNamespace(logger=logging.getLogger(__name__))):
    helpers.apply_pending_steer_to_tool_results(agent,messages,1)
    assert messages[-1]['role']=='user' and 'first correction' in messages[-1]['content']
    assert 'second correction' in messages[-1]['content']
    helpers.apply_pending_steer_to_tool_results(agent,messages,1)
    assert len(messages)==3 and agent._drain_pending_steer() is None
print('Native tool-boundary consumption passed')
