"""Materialize first-party image references via authenticated Warehouse MCP."""
import json
import re
from pathlib import Path


def managed_reference(value):
    return isinstance(value,str) and value.startswith((
        'calliope-image:', '/api/calliope/generated-images/', '/captures/',
        '/app/data/captures/', '/home/kasm-user/Shared/'))


def materialize(reference, task_id=None):
    # Use the normal MCP dispatcher: it supplies trusted run/session identity.
    # No HTTP credential forwarding or guessed public hostnames.
    import model_tools
    result=model_tools.handle_function_call('mcp__Warehouse__read_calliope_image',
        {'reference':reference},task_id=task_id)
    if isinstance(result,str):
        try:result=json.loads(result)
        except ValueError:pass
    def strings(value):
        if isinstance(value,str):yield value
        elif isinstance(value,dict):
            for v in value.values():yield from strings(v)
        elif isinstance(value,list):
            for v in value:yield from strings(v)
    for text in strings(result):
        for match in re.finditer(r'^MEDIA:[ \t]*([^\r\n]+)$',text,re.MULTILINE):
            path=Path(match[1].strip())
            if path.is_absolute() and path.is_file():return str(path)
    raise ValueError('Managed image could not be read. Use read_calliope_image with the saved reference, or look_at_desktop for a fresh screen. Do not regenerate the image or sign in again.')
