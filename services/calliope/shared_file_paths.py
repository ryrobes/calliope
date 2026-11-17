"""Portable path rules for conversation files on the appliance and desktop."""
from pathlib import Path, PurePosixPath
import re
import uuid

MAX_BYTES = 50 * 1024 * 1024
DESKTOP_ROOT = '/home/kasm-user/Shared'


def session_key(value):
    return str(uuid.UUID(str(value)))


def relative_name(value):
    name = str(value or '')
    parts = name.split('/')
    if (not name or len(name.encode()) > 700 or len(parts) > 16
            or any(p in {'', '.', '..'} or len(p.encode()) > 200 for p in parts)
            or re.search(r'[\\\x00-\x1f\x7f]', name)):
        raise ValueError('Use a relative filename or folder path without .. or control characters.')
    return str(PurePosixPath(name))


def contained(root, name):
    root = Path(root)
    name = relative_name(name)
    path = root / name
    # Never follow guest-created symlinks, including ones pointing inside root.
    if root.is_symlink() or any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('Symlinks are not shared files.')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('File is outside the shared folder.')
    return path


def desktop_folder(session_id):
    return f'{DESKTOP_ROOT}/{session_key(session_id)}'
