"""Resolve managed references before either native or auxiliary vision reads."""
import os
from pathlib import Path
root=Path(os.environ.get('HERMES_SOURCE_ROOT','/opt/hermes'))
p=root/'tools/vision_tools.py';s=p.read_text()
old='    from tools.image_source import ImageResolutionError, ResolveContext, resolve_image_source\n    try:\n'
new='''    from tools.image_source import ImageResolutionError, ResolveContext, resolve_image_source
    from gateway.calliope_managed_images import managed_reference, materialize
    if managed_reference(image_url):
        image_url = await asyncio.to_thread(materialize, image_url, task_id)
    try:
'''
assert s.count(old)==1,'Managed image vision seam changed'
p.write_text(s.replace(old,new))
