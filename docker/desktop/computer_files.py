"""Persistent shared folders; initial uploads never overwrite desktop edits."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import uuid
import ctypes
import errno
import shutil

from shared_file_paths import MAX_BYTES, DESKTOP_ROOT, contained, relative_name, session_key
from computer_runtime import ComputerError

LOCK = threading.Lock()


def rename_exclusive(source, target):
    """Linux renameat2 prevents replacing a file created concurrently by an app."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise ComputerError('FILE_EXISTS', 'That name already exists. Choose another name.', 409)
        raise OSError(code, os.strerror(code))


class SharedFiles:
    def __init__(self, root=DESKTOP_ROOT, receipts='/var/lib/calliope-desktop/shared-uploads'):
        self.root, self.receipts = Path(root), Path(receipts)

    def folder(self, sid):
        return contained(self.root, session_key(sid))

    def path(self, sid, name):
        return contained(self.folder(sid), name)

    def listing(self, sid):
        root = self.folder(sid)
        files = []
        origins = {item.get('current_name', item['name']): fid for fid, item in self.uploads(sid)}
        if root.exists():
            for base, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.upload-') and not (Path(base)/d).is_symlink())
                for name in dirs + sorted(names):
                    p = Path(base)/name
                    if p.is_symlink() or not (p.is_file() or p.is_dir()) or name.startswith('.upload-'):
                        continue
                    try: stat = p.stat()
                    except FileNotFoundError: continue  # A desktop app may move it during refresh.
                    relative = str(p.relative_to(root))
                    files.append({'name': relative, 'bytes': stat.st_size if p.is_file() else 0,
                                  'type': 'folder' if p.is_dir() else 'file',
                                  'original_id': origins.get(relative),
                                  'modified_at': stat.st_mtime, 'status': 'ready'})
                    if len(files) >= 5000:
                        return {'files': files, 'truncated': True}
        return {'files': files, 'truncated': False}

    def uploads(self, sid):
        return [(p.stem, json.loads(p.read_text())) for p in (self.receipts/session_key(sid)).glob('*.json')]

    def mutate(self, sid, operation, name, target=None):
        sid, name = session_key(sid), relative_name(name)
        if operation not in {'mkdir', 'rename', 'move', 'copy', 'trash'}:
            raise ValueError('Unknown file operation.')
        with LOCK:
            source = self.path(sid, name)
            if operation == 'mkdir':
                self.folder(sid).mkdir(parents=True, exist_ok=True)
                if not source.parent.is_dir():
                    raise ValueError('Parent folder no longer exists. Refresh Files.')
                if source.exists():
                    raise ComputerError('FILE_EXISTS', 'That name already exists.', 409)
                source.mkdir()
                return {'name': name, 'operation': operation}
            if not source.exists():
                raise ComputerError('FILE_NOT_FOUND', 'File no longer exists. Refresh Files.', 404)
            if operation == 'trash':
                trash = self.receipts/'trash'/sid/str(uuid.uuid4())
                trash.mkdir(parents=True)
                (trash/'receipt.json').write_text(json.dumps({'name': name}))
                # Home and runtime can be separate volumes. Copy first, then remove;
                # never remove the source if copying failed.
                shutil.move(str(source), str(trash/'content'))
                target = None
            else:
                target = relative_name(target)
                destination = self.path(sid, target)
                if destination == source or destination.is_relative_to(source):
                    raise ValueError('Choose a destination outside the source folder.')
                if not destination.parent.is_dir():
                    raise ValueError('Destination folder no longer exists. Refresh Files.')
                if operation == 'copy':
                    # Reject links anywhere in a copied tree, including broken links.
                    if source.is_dir() and any(p.is_symlink() for p in source.rglob('*')):
                        raise ValueError('Folders containing symlinks cannot be copied here.')
                    temporary = destination.parent/('.upload-'+uuid.uuid4().hex)
                    try:
                        if source.is_dir(): shutil.copytree(source, temporary, symlinks=True)
                        else: shutil.copy2(source, temporary, follow_symlinks=False)
                        rename_exclusive(temporary, destination)
                    finally:
                        if temporary.is_dir(): shutil.rmtree(temporary)
                        else: temporary.unlink(missing_ok=True)
                else:
                    rename_exclusive(source, destination)
            if operation != 'copy':
                for fid, item in self.uploads(sid):
                    current = item.get('current_name', item['name'])
                    if current and (current == name or current.startswith(name+'/')):
                        item['current_name'] = target+current[len(name):] if target else None
                        receipt = self.receipts/sid/(fid+'.json')
                        temp = receipt.with_suffix('.part')
                        temp.write_text(json.dumps(item)); temp.replace(receipt)
            return {'name': target, 'operation': operation}

    def put(self, sid, name, upload_id, expected_sha, stream, size):
        if size < 0 or size > MAX_BYTES:
            raise ComputerError('FILE_TOO_LARGE', 'File exceeds 50 MB.', 413)
        upload_id = str(uuid.UUID(str(upload_id)))
        sid, name = session_key(sid), relative_name(name)
        with LOCK:
            receipt = self.receipts / sid / (upload_id + '.json')
            receipt.parent.mkdir(parents=True, exist_ok=True)
            if receipt.exists():
                prior = json.loads(receipt.read_text())
                if prior.get('name') != name or prior.get('sha256') != expected_sha:
                    raise ComputerError('UPLOAD_CONFLICT', 'Upload identity changed.', 409)
                return {'status': 'ready', 'name': name, 'already_delivered': True}
            path = self.path(sid, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                # Recover a crash after atomic publication but before the receipt.
                with path.open('rb') as f:
                    current_sha = hashlib.file_digest(f, 'sha256').hexdigest()
                if current_sha != expected_sha:
                    raise ComputerError('FILE_EXISTS', 'A different desktop file already uses this name.', 409)
            else:
                fd, temp = tempfile.mkstemp(prefix='.upload-', dir=path.parent)
                try:
                    digest = hashlib.sha256()
                    with os.fdopen(fd, 'wb') as f:
                        remaining = size
                        while remaining:
                            data = stream.read(min(remaining, 262144))
                            if not data:
                                raise ValueError('Incomplete file upload.')
                            f.write(data); digest.update(data); remaining -= len(data)
                        f.flush(); os.fsync(f.fileno())
                    if digest.hexdigest() != expected_sha:
                        raise ValueError('Upload checksum mismatch.')
                    os.link(temp, path)  # Exclusive publication, even if an app writes concurrently.
                finally:
                    Path(temp).unlink(missing_ok=True)
            temp_receipt = receipt.with_suffix('.part')
            temp_receipt.write_text(json.dumps({'name': name, 'sha256': expected_sha}))
            temp_receipt.replace(receipt)
            return {'status': 'ready', 'name': name}
