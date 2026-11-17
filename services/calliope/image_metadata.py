"""Bounded, provenance-backed image descriptions; no vision calls or guessed links."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from uuid import UUID


LIMITS = {
    'title': 160, 'purpose': 100, 'generation_brief': 700,
    'edit_instructions': 500, 'filename': 160, 'creator': 100,
    'creator_session': 100, 'origin': 60, 'model': 100,
    'image_id': 36, 'source_surface_id': 36, 'parent_surface_id': 36,
    'same_image_as': 36, 'artifact': 180, 'content_sha256': 64,
    'file_modified_at': 40,
}


def text(value, limit):
    return re.sub(r'\s+', ' ', value).strip()[:limit] if isinstance(value, str) else ''


def bounded(value):
    if not isinstance(value, dict):
        return {}
    result = {k: text(value.get(k), n) for k, n in LIMITS.items()}
    result = {k: v for k, v in result.items() if v}
    if result.get('generation_brief'):
        result['brief_kind'] = 'requested_content_not_verified_caption'
    return result


def generated(prompt, *, title='', purpose='', model='', creator_session=''):
    brief = text(prompt, 700)
    return bounded({'title': text(title, 160) or ('Image · ' + brief[:110] if brief else 'Generated image'),
                    'purpose': purpose or 'generated_image', 'generation_brief': brief,
                    'model': model, 'creator_session': creator_session, 'origin': 'generated_image'})


def describe(row):
    payload = row.get('payload') if isinstance(row.get('payload'), dict) else {}
    source = row.get('source') if isinstance(row.get('source'), dict) else {}
    if row.get('kind') != 'image' and not str(payload.get('mime_type') or '').startswith('image/'):
        return {}
    saved = bounded(payload.get('image_metadata'))
    meta = dict(saved)
    meta.setdefault('title', row.get('title'))
    meta.setdefault('purpose', 'saved_image' if row.get('kind') == 'document' else 'image')
    meta.setdefault('origin', source.get('origin'))
    for field in ('image_id', 'model', 'content_sha256', 'file_modified_at'):
        if payload.get(field):
            meta[field] = str(payload[field])
    if payload.get('original_name'):
        meta['filename'] = PurePosixPath(str(payload['original_name'])).name
    creator = source.get('specialist_key') or source.get('hermes_profile')
    if creator:
        meta['creator'] = creator
    if row.get('parent_surface_id'):
        meta['parent_surface_id'] = str(row['parent_surface_id'])
    if source.get('source_surface_id'):
        meta['source_surface_id'] = str(source['source_surface_id'])
    slug = row.get('artifact_slug') or source.get('artifact_slug')
    version = row.get('artifact_version') or source.get('artifact_version')
    if slug:
        meta['artifact'] = f'{slug}@v{version}' if version else str(slug)
    if source.get('origin') == 'user_visual_intent':
        meta['purpose'] = 'visual_intent'
        instructions = source.get('instructions')
        if isinstance(instructions, list):
            meta['edit_instructions'] = ' | '.join(text(v, 250) for v in instructions[-2:])
    elif row.get('tool_name') == 'capture_live_app' or str(row.get('lineage_key') or '').startswith('capture:'):
        meta['purpose'] = 'page_capture'
    return bounded(meta)


def inherit_copy(row, original):
    """Only call after verifying exact bytes and same-session membership."""
    payload = dict(row.get('payload') or {})
    original_meta = describe(original)
    own = bounded(payload.get('image_metadata'))
    if original_meta:
        payload['image_metadata'] = bounded({**original_meta, **own, 'same_image_as': str(original['id'])})
    return {**row, 'payload': payload}


def prepare(conn, session_id, row, parent_id=None):
    """Persist the shared descriptor alongside an image's normal Stage receipt."""
    if not describe(row):
        return row
    payload = dict(row.get('payload') or {})
    digest = str(payload.get('content_sha256') or '')
    if re.fullmatch('[a-f0-9]{64}', digest) and not bounded(payload.get('image_metadata')).get('generation_brief'):
        original = conn.execute(
            "SELECT id,kind,title,payload,source,artifact_slug,artifact_version FROM rvbbit.calliope_surfaces "
            "WHERE session_id=%s::uuid AND payload->>'content_sha256'=%s "
            "AND payload->'image_metadata'->>'generation_brief' IS NOT NULL "
            "ORDER BY created_at,id LIMIT 1", (session_id, digest)).fetchone()
        if original:
            row = inherit_copy(row, original)
            payload = dict(row['payload'])
    payload['image_metadata'] = describe({**row, 'parent_surface_id': parent_id})
    return {**row, 'payload': payload}


def enrich_rows(conn, session_id, rows):
    """Read-only legacy enrichment. Batch queries, no PNG reads or cross-thread search."""
    rows = [{**r, 'payload': dict(r.get('payload') or {})} for r in rows]
    ids = []
    for row in rows:
        payload = row['payload']
        if not describe(row) or bounded(payload.get('image_metadata')).get('generation_brief'):
            continue
        try:
            ids.append(str(UUID(str(payload.get('image_id')))))
        except (ValueError, TypeError, AttributeError):
            pass
    if ids and (conn.execute("SELECT to_regclass('rvbbit.generated_images') AS r").fetchone() or {}).get('r'):
        images = conn.execute('SELECT id,prompt,model FROM rvbbit.generated_images WHERE id=ANY(%s::uuid[])', (ids,)).fetchall()
        by_id = {str(r['id']): r for r in images}
        for row in rows:
            image = by_id.get(str(row['payload'].get('image_id')))
            if image:
                old = bounded(row['payload'].get('image_metadata'))
                row['payload']['image_metadata'] = {**generated(image.get('prompt'), model=image.get('model')), **old}
    hashes = list({r['payload'].get('content_sha256') for r in rows if describe(r)
                   and not bounded(r['payload'].get('image_metadata')).get('generation_brief')
                   and re.fullmatch('[a-f0-9]{64}', str(r['payload'].get('content_sha256') or ''))})
    if hashes:
        originals = conn.execute(
            "SELECT DISTINCT ON (payload->>'content_sha256') id,kind,title,payload,source,artifact_slug,artifact_version "
            "FROM rvbbit.calliope_surfaces WHERE session_id=%s::uuid "
            "AND payload->>'content_sha256'=ANY(%s::text[]) AND payload->'image_metadata'->>'generation_brief' IS NOT NULL "
            "ORDER BY payload->>'content_sha256',created_at,id", (session_id, hashes)).fetchall()
        by_hash = {r['payload']['content_sha256']: r for r in originals}
        rows = [inherit_copy(r, by_hash[r['payload']['content_sha256']])
                if r['payload'].get('content_sha256') in by_hash and str(r.get('id')) != str(by_hash[r['payload']['content_sha256']]['id'])
                else r for r in rows]
    return rows
