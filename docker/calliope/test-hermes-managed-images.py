"""Exercise the real Hermes MCP media renderer and both vision preparation lanes."""
import asyncio
import base64
import io
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from tools import vision_tools
from tools.mcp_tool_handlers import _render_content_blocks
from gateway.calliope_managed_images import materialize

raw=io.BytesIO();Image.new('RGB',(12,9)).save(raw,format='PNG');png=raw.getvalue()
with tempfile.TemporaryDirectory() as temp:
    image=Path(temp)/'cached.png';image.write_bytes(png)
    block=SimpleNamespace(type='image',data=base64.b64encode(png).decode(),mimeType='image/png',text=None)
    with patch('gateway.platforms.base.cache_image_from_bytes',return_value=str(image)):
        rendered,count=_render_content_blocks(SimpleNamespace(content=[block]),'Warehouse')
    assert count==1 and rendered=='MEDIA:'+str(image)
    calls=[]
    def dispatch(name,args,**kwargs):
        calls.append((name,args,kwargs));return rendered
    async def check():
        for native in (True,False):
            prepared=await vision_tools._prepare_image('/api/calliope/generated-images/33bcab2e-1a89-49e9-97a4-e11f2f82f3df',
                'task',None,validate_decode=native)
            assert prepared.path.is_file() and prepared.mime=='image/png'
            prepared.path.unlink()
    with patch('model_tools.handle_function_call',side_effect=dispatch):
        asyncio.run(check())
    assert len(calls)==2 and all(c[0]=='mcp__Warehouse__read_calliope_image' for c in calls)
    assert all(c[2]['task_id']=='task' for c in calls)
print('Native MCP image cache -> native/aux vision preparation: passed')
