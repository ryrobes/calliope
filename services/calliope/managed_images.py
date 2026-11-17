"""Read portable image references through Calliope's existing identity boundary."""
from pathlib import Path
import re

from shared_file_paths import DESKTOP_ROOT, MAX_BYTES, relative_name, session_key


def reference(value, capture_root):
    value=str(value or '')
    match=re.fullmatch(r'(?:calliope-image:|/api/calliope/generated-images/)([0-9a-fA-F-]{36})',value)
    if match:return 'generated',session_key(match[1])
    if value.startswith(DESKTOP_ROOT+'/'):
        sid,sep,name=value[len(DESKTOP_ROOT)+1:].partition('/')
        if not sep:raise ValueError('An image filename is required.')
        return 'desktop',(session_key(sid),relative_name(name))
    root=Path(capture_root).resolve()
    if value.startswith('/captures/'):
        path=root/relative_name(value[len('/captures/'):])
    else:
        path=Path(value)
        if not path.is_absolute():raise ValueError('Use a capture, generated image, or conversation Shared image reference.')
    if not path.resolve().is_relative_to(root):raise ValueError('Image is outside the managed capture folder.')
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Image symlinks are not supported.')
    return 'capture',path


def validate(data):
    if len(data)>MAX_BYTES:raise ValueError('Image exceeds the shared-file size limit.')
    # FastMCP needs the correct media type. Never return HTML/login bodies as images.
    import io
    from PIL import Image
    with Image.open(io.BytesIO(data)) as image:
        fmt=image.format
        if fmt not in ('PNG','JPEG','WEBP','GIF'):raise ValueError('Use PNG, JPEG, WebP or GIF.')
        image.verify()
    return data,{'PNG':'png','JPEG':'jpeg','WEBP':'webp','GIF':'gif'}[fmt]


def read(value, *, capture_root, conn_factory, config, actor):
    kind,target=reference(value,capture_root)
    if kind=='generated':
        with conn_factory() as conn:
            row=conn.execute('SELECT png FROM rvbbit.generated_images WHERE id=%s::uuid AND lower(owner_email)=lower(%s)',(target,actor)).fetchone()
        if not row:raise ValueError('Generated image unavailable to this user.')
        return validate(bytes(row['png']))
    if kind=='capture':
        if target.stat().st_size>MAX_BYTES:raise ValueError('Image exceeds the shared-file size limit.')
        return validate(target.read_bytes())
    sid,name=target
    import calliope_access
    from desktop_runtime import DesktopRuntime
    import httpx
    with conn_factory() as conn:calliope_access.require_view(conn,sid,actor)
    runtime=DesktopRuntime(config)
    data=bytearray()
    with httpx.Client(timeout=120,trust_env=False) as client:
        with client.stream('GET',runtime.base+'/computer/shared/download',
                           params={'session_id':sid,'path':name},headers=runtime.headers) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data)>MAX_BYTES:raise ValueError('Image exceeds the shared-file size limit.')
    return validate(bytes(data))
