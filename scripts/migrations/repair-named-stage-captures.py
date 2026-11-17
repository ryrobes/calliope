#!/usr/bin/env python3
"""Recover legacy cwd captures, running inside calliope-server with PYTHONPATH=/app.

Dry-run by default; --session UUID is required. --apply writes a private rollback
ledger under Calliope's durable file root before repairing any rows. Never
re-captures a live page or substitutes a file overwritten after its receipt.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4


def candidate(row, cwd, max_bytes):
    payload = row['payload']
    raw = str(payload.get('path') or '')
    path = Path(raw)
    # Only the old named-file bug, never arbitrary paths or symlink escapes.
    if not raw or path.is_absolute() or len(path.parts) != 1 or path.suffix.lower() not in {'.png', '.jpg', '.jpeg'}:
        return None, 'outside_named_capture_scope'
    path = cwd / path
    if path.is_symlink():
        return None, 'symlink'
    try:
        before = path.stat()
        if before.st_size <= 0 or before.st_size > max_bytes or before.st_size != payload.get('bytes'):
            return None, 'size_mismatch'
        if before.st_mtime > row['created_at'].timestamp():
            return None, 'overwritten_after_receipt'
        data = path.read_bytes()
        after = path.stat()
        if (before.st_size,before.st_mtime_ns,before.st_ino)!=(after.st_size,after.st_mtime_ns,after.st_ino):
            return None, 'changed_during_read'
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    except (OSError, ValueError):
        return None, 'missing_or_invalid_image'
    return (path,data,before), 'recoverable'


def main():
    import calliope, server
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session',required=True,type=UUID)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    config=calliope.CalliopeConfig.from_env()
    with server._conn() as conn:
        # Rank globally before scoping: a shared filename might have been reused
        # by a different thread. Only its latest receipt can own current bytes.
        rows=conn.execute("""WITH newest AS (
          SELECT DISTINCT ON (payload->>'path') * FROM rvbbit.calliope_surfaces
          WHERE kind='image' AND tool_name LIKE '%%capture_live_app'
            AND payload->>'path' IS NOT NULL
          ORDER BY payload->>'path',created_at DESC,ordinal DESC
        ) SELECT * FROM newest WHERE session_id=%s::uuid
          AND payload->>'storage_path' IS NULL""",(str(args.session),)).fetchall()
    summary=Counter();backup=None
    for row in rows:
        found,status=candidate(row,Path.cwd(),config.max_image_bytes)
        summary[status]+=1
        if not found or not args.apply:
            continue
        path,data,stat=found
        digest=hashlib.sha256(data).hexdigest()
        target=config.file_root/'captures'/str(row['session_id'])/digest[:20]/path.name
        with server._conn() as conn:
            current=conn.execute('SELECT payload FROM rvbbit.calliope_surfaces WHERE id=%s FOR UPDATE',(row['id'],)).fetchone()
            if not current or current['payload']!=row['payload']:
                summary['changed_receipt_skipped']+=1
                continue
            if backup is None:
                folder=config.file_root/'repair-backups'
                folder.mkdir(parents=True,exist_ok=True)
                backup=folder/f'named-captures-{uuid4().hex}.jsonl'
            fd=os.open(backup,os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
            with os.fdopen(fd,'a') as stream:
                stream.write(json.dumps({'id':str(row['id']),'payload':row['payload']})+'\n')
                stream.flush();os.fsync(stream.fileno())
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            payload={**row['payload'],'storage_path':str(target.resolve()),
                     'content_sha256':digest,'file_modified_at':datetime.fromtimestamp(stat.st_mtime,timezone.utc).isoformat()}
            conn.execute('UPDATE rvbbit.calliope_surfaces SET payload=%s::jsonb WHERE id=%s', (json.dumps(payload),row['id']))
            summary['repaired']+=1
    print(json.dumps({'apply':args.apply,'session':str(args.session),'counts':summary,'backup':str(backup) if backup else None}))


if __name__=='__main__':
    main()
