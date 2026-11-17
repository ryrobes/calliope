"""Private image-edit drafts. Saving publishes a reference, never a chat turn."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import uuid
from typing import Any

import expert_portraits
from PIL import Image
from starlette.responses import Response

MAX_BYTES = 8_000_000
DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_visual_intents (
 id uuid PRIMARY KEY, session_id uuid NOT NULL REFERENCES rvbbit.calliope_sessions(id) ON DELETE CASCADE,
 owner_email text NOT NULL, source_surface_id uuid NOT NULL REFERENCES rvbbit.calliope_surfaces(id) ON DELETE CASCADE,
 parent_id uuid REFERENCES rvbbit.calliope_visual_intents(id) ON DELETE CASCADE,
 status text NOT NULL CHECK(status IN ('original','running','ready','failed')),
 prompt text NOT NULL DEFAULT '', image bytea, annotation bytea,
 model text, usage jsonb NOT NULL DEFAULT '{}', error text,
 saved_surface_id uuid REFERENCES rvbbit.calliope_surfaces(id) ON DELETE SET NULL,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX IF NOT EXISTS calliope_visual_intents_session_idx
 ON rvbbit.calliope_visual_intents(session_id,owner_email,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS calliope_visual_intents_saved_idx
 ON rvbbit.calliope_visual_intents(saved_surface_id) WHERE saved_surface_id IS NOT NULL;
"""


def image_bytes(value: str) -> bytes:
    if not isinstance(value, str) or len(value) > MAX_BYTES * 4 // 3 + 100:
        raise ValueError('Image exceeds the 8 MB limit.')
    if not value.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
        raise ValueError('Use a PNG, JPEG or WebP image.')
    try:
        data = base64.b64decode(value.split(',', 1)[1], validate=True)
        validate_image(data)
    except Exception as exc:
        raise ValueError('The image could not be read, or is too large.') from exc
    return data


def validate_image(data: bytes) -> None:
    if not data or len(data) > MAX_BYTES:
        raise ValueError('Image exceeds the 8 MB limit.')
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ('PNG', 'JPEG', 'WEBP') or image.width * image.height > 25_000_000:
            raise ValueError('Unsupported image size or format.')
        image.verify()


def edit_config(conn) -> dict:
    config = expert_portraits._config(conn)
    if config and config.get('lane') == 'hermes':
        # OpenRouter model IDs stored in the shared image slot can be invoked
        # directly. Never start a Hermes session just to perform an image edit.
        key = os.getenv('OPENROUTER_API_KEY', '')
        if '/' in config['model'] and key:
            config = {'model': config['model'], 'base_url': 'https://openrouter.ai/api/v1', 'api_key': key}
    if not config or config.get('lane') == 'hermes' or 'openrouter.ai' not in config['base_url']:
        raise ValueError('Visual Intent needs an OpenRouter image-edit model in Models & agents → Image generation, and its server credential. The Hermes lane can use an OpenRouter model ID with OPENROUTER_API_KEY.')
    return config


def generate(config: dict, original: bytes, annotated: bytes | None, prompt: str) -> tuple[bytes, dict]:
    import urllib.request
    content: list[dict] = [{'type': 'text', 'text': (
        'Edit the supplied page design image according to the user instruction. '
        'The first image is the clean current design. If present, the second is the same design '
        'with user markup indicating intent, not artwork to reproduce. Return one clean design image '
        'without annotation strokes. Preserve content, values, labels, aspect ratio and unrelated '
        'design choices unless the instruction asks to change them. Do not apply an additional design profile.\n\n'
        'User instruction:\n' + prompt
    )}]
    for data in [original, annotated]:
        if data:
            content.append({'type': 'image_url', 'image_url': {'url':
                f'data:{expert_portraits.image_media_type(data)};base64,' + base64.b64encode(data).decode()}})
    request = urllib.request.Request(config['base_url'] + '/chat/completions',
        data=json.dumps({'model': config['model'], 'messages': [{'role': 'user', 'content': content}],
                         'modalities': ['image', 'text']}).encode(),
        headers={'Authorization': f"Bearer {config['api_key']}", 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        payload = json.loads(response.read(24_000_000))
    images = payload.get('choices', [{}])[0].get('message', {}).get('images', [])
    url = images[0].get('image_url', {}).get('url', '') if images else ''
    # Image-output OpenRouter responses carry data URIs; do not fetch arbitrary URLs.
    result = image_bytes(url)
    return result, payload.get('usage') or {}


def public(row: dict) -> dict:
    return {key: str(value) if isinstance(value, uuid.UUID) else value for key, value in row.items()
            if key not in {'image', 'annotation', 'owner_email', 'has_image'}} | {
        'image_url': f"/api/calliope/sessions/{row['session_id']}/visual-intent/{row['id']}/image"
        if row.get('image') or row.get('has_image') else None}


def is_image_source(surface):
    """Agent PNGs can be file/document surfaces rather than image surfaces."""
    if not surface:
        return False
    if surface['kind'] == 'image':
        return True
    if surface['kind'] != 'document':
        return False
    payload = surface.get('payload') or {}
    if payload.get('provider') in ('google_docs', 'google_sheets'):
        return False
    mime = str(payload.get('mime_type') or payload.get('content_type') or '').lower()
    filename = str(payload.get('filename') or payload.get('original_name') or surface.get('title') or '')
    extension = filename.split('?', 1)[0].rsplit('.', 1)[-1].lower()
    return mime.startswith('image/') or extension in {'png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg', 'bmp', 'ico'}


def selected_attachment(conn_factory, config, session_id, surface_id, decoded):
    """Explicit selection carries pixels into this turn and its specialist handoffs."""
    if not surface_id:
        return decoded
    with conn_factory() as conn:
        row = conn.execute("SELECT j.image,s.title FROM rvbbit.calliope_visual_intents j "
            "JOIN rvbbit.calliope_surfaces s ON s.id=j.saved_surface_id "
            "WHERE s.id=%s::uuid AND s.session_id=%s::uuid AND j.session_id=s.session_id AND j.status='ready'",
            (surface_id, session_id)).fetchone()
    if not row:
        return decoded
    data = bytes(row['image'])
    # The saved reference counts against the same four-image and byte limits.
    used = sum(len(item['raw']) + len((item.get('annotation') or {}).get('overlay_raw', b'')) for item in decoded)
    if len(decoded) >= 4 or used + len(data) > config.max_image_bytes:
        raise ValueError('The selected visual reference exceeds this message’s image allowance. Remove another image or deselect the reference.')
    mime = expert_portraits.image_media_type(data)
    return [*decoded, {'name': str(row['title'])[:160], 'mime': mime, 'raw': data,
                       'data_url': f'data:{mime};base64,' + base64.b64encode(data).decode()}]


def register(mcp, conn_factory, api_owner, json_response):
    import calliope
    tasks: set[asyncio.Task] = set()

    def authorize(request):
        owner, error = api_owner(request)
        if error:
            return None, None, error
        sid = calliope._uuid(request.path_params.get('session_id'))
        session = calliope._session_for_participant(conn_factory, sid, owner) if sid else None
        return owner, sid, None if session else json_response({'error': {'message': 'Notebook not found.'}}, 404)

    async def run(job_id, config):
        def work():
            with conn_factory() as conn:
                row = conn.execute('SELECT j.*,p.image AS base FROM rvbbit.calliope_visual_intents j '
                    'JOIN rvbbit.calliope_visual_intents p ON p.id=j.parent_id WHERE j.id=%s', (job_id,)).fetchone()
            if not row:
                return
            try:
                result, usage = generate(config, bytes(row['base']), bytes(row['annotation']) if row['annotation'] else None, row['prompt'])
                with conn_factory() as conn:
                    conn.execute("UPDATE rvbbit.calliope_visual_intents SET status='ready',image=%s,usage=%s::jsonb,finished_at=now() WHERE id=%s AND status='running'",
                                 (result, json.dumps(usage), job_id))
            except Exception:
                # Provider exceptions can embed request data/credentials. Store a safe explanation.
                with conn_factory() as conn:
                    conn.execute("UPDATE rvbbit.calliope_visual_intents SET status='failed',error=%s,finished_at=now() WHERE id=%s AND status='running'",
                        ('Image editing failed or timed out. Your base image, instruction and markup are retained; try again.', job_id))
        await asyncio.to_thread(work)

    @mcp.custom_route('/api/calliope/sessions/{session_id}/visual-intent', methods=['GET', 'POST'])
    async def drafts(request):
        owner, sid, error = authorize(request)
        if error:
            return error
        if request.method == 'GET':
            with conn_factory() as conn:
                conn.execute("UPDATE rvbbit.calliope_visual_intents SET status='failed',error='Generation was interrupted. Please retry.' "
                             "WHERE session_id=%s AND owner_email=%s AND status='running' AND created_at<now()-interval '10 minutes'", (sid, owner))
                rows = conn.execute('SELECT j.id,j.session_id,j.source_surface_id,j.parent_id,j.status,j.prompt,j.model,j.usage,j.error,j.saved_surface_id,j.created_at,j.finished_at,j.image IS NOT NULL AS has_image,s.artifact_slug AS source_artifact_slug,s.artifact_version AS source_artifact_version FROM rvbbit.calliope_visual_intents j JOIN rvbbit.calliope_surfaces s ON s.id=j.source_surface_id WHERE j.session_id=%s AND j.owner_email=%s ORDER BY j.created_at,j.id', (sid, owner)).fetchall()
            return json_response({'drafts': [public(row) for row in rows]})
        try:
            raw = await request.body()
            if len(raw) > 12_000_000:
                raise ValueError('Image request is too large.')
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError('Expected an image-edit request.')
            draft_id = calliope._uuid(body.get('id'))
            if not draft_id:
                raise ValueError('A draft ID is required.')
            parent_id = calliope._uuid(body.get('parent_id'))
            with conn_factory() as conn:
                with conn.transaction():
                    conn.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('visual-intent:' + owner,))
                    conn.execute("UPDATE rvbbit.calliope_visual_intents SET status='failed',error='Generation was interrupted. Please retry.' WHERE owner_email=%s AND status='running' AND created_at<now()-interval '10 minutes'", (owner,))
                    existing = conn.execute('SELECT * FROM rvbbit.calliope_visual_intents WHERE id=%s AND owner_email=%s AND session_id=%s', (draft_id, owner, sid)).fetchone()
                    if existing:
                        return json_response({'draft': public(existing)})
                    if parent_id:
                        parent = conn.execute("SELECT * FROM rvbbit.calliope_visual_intents WHERE id=%s AND session_id=%s AND owner_email=%s AND status IN ('original','ready')", (parent_id, sid, owner)).fetchone()
                        if not parent:
                            raise ValueError('Choose a completed image as the edit base.')
                        running = conn.execute("SELECT count(*) AS n FROM rvbbit.calliope_visual_intents WHERE owner_email=%s AND status='running'", (owner,)).fetchone()['n']
                        if running >= 2:
                            raise ValueError('Two image edits are already running. Wait for one to finish.')
                        prompt = str(body.get('prompt') or '').strip()
                        if not prompt or len(prompt) > 8000:
                            raise ValueError('Describe the change in 1–8,000 characters.')
                        annotation = image_bytes(body['annotation']) if body.get('annotation') else None
                        config = edit_config(conn)
                        row = conn.execute("INSERT INTO rvbbit.calliope_visual_intents(id,session_id,owner_email,source_surface_id,parent_id,status,prompt,annotation,model) "
                            "VALUES(%s,%s,%s,%s,%s,'running',%s,%s,%s) RETURNING *",
                            (draft_id, sid, owner, parent['source_surface_id'], parent_id, prompt, annotation, config['model'])).fetchone()
                    else:
                        source_id = calliope._uuid(body.get('source_surface_id'))
                        source = conn.execute("SELECT id,kind,title,payload FROM rvbbit.calliope_surfaces WHERE id=%s AND session_id=%s AND kind IN ('image','document')", (source_id, sid)).fetchone() if source_id else None
                        if not is_image_source(source):
                            raise ValueError('Choose an image from this notebook.')
                        image = image_bytes(body.get('image'))
                        row = conn.execute("INSERT INTO rvbbit.calliope_visual_intents(id,session_id,owner_email,source_surface_id,status,image) VALUES(%s,%s,%s,%s,'original',%s) RETURNING *",
                                           (draft_id, sid, owner, source_id, image)).fetchone()
            if parent_id:
                task = asyncio.create_task(run(draft_id, config))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            return json_response({'draft': public(row)}, 202 if parent_id else 201)
        except (ValueError, TypeError) as exc:
            return json_response({'error': {'message': str(exc)}}, 400)

    @mcp.custom_route('/api/calliope/sessions/{session_id}/visual-intent/{draft_id}/image', methods=['GET'])
    async def image(request):
        owner, sid, error = authorize(request)
        if error:
            return error
        draft_id = calliope._uuid(request.path_params['draft_id'])
        with conn_factory() as conn:
            row = conn.execute('SELECT image FROM rvbbit.calliope_visual_intents WHERE id=%s AND session_id=%s AND (owner_email=%s OR saved_surface_id IS NOT NULL)', (draft_id, sid, owner)).fetchone() if draft_id else None
        if not row or not row['image']:
            return Response(status_code=404)
        data = bytes(row['image'])
        return Response(data, media_type=expert_portraits.image_media_type(data), headers={'Cache-Control': 'private, max-age=86400', 'X-Content-Type-Options': 'nosniff'})

    @mcp.custom_route('/api/calliope/sessions/{session_id}/visual-intent/{draft_id}/save', methods=['POST'])
    async def save(request):
        owner, sid, error = authorize(request)
        if error:
            return error
        draft_id = calliope._uuid(request.path_params['draft_id'])
        with conn_factory() as conn:
            with conn.transaction():
                row = conn.execute('SELECT * FROM rvbbit.calliope_visual_intents WHERE id=%s AND session_id=%s AND owner_email=%s FOR UPDATE', (draft_id, sid, owner)).fetchone() if draft_id else None
                if not row or row['status'] != 'ready':
                    return json_response({'error': {'message': 'Choose a completed generated image.'}}, 400)
                if row['saved_surface_id']:
                    surface = conn.execute('SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s', (row['saved_surface_id'],)).fetchone()
                    return json_response({'surface': calliope._surface_json(surface)})
                source = conn.execute('SELECT * FROM rvbbit.calliope_surfaces WHERE id=%s AND session_id=%s', (row['source_surface_id'], sid)).fetchone()
                history = conn.execute('WITH RECURSIVE chain AS (SELECT id,parent_id,prompt,created_at FROM rvbbit.calliope_visual_intents WHERE id=%s '
                    'UNION ALL SELECT p.id,p.parent_id,p.prompt,p.created_at FROM rvbbit.calliope_visual_intents p JOIN chain c ON p.id=c.parent_id) '
                    'SELECT id,prompt FROM chain ORDER BY created_at,id', (draft_id,)).fetchall()
                # Keep a local media copy: selected image context and specialist handoffs
                # already consume storage_path; a URL alone is not sufficient.
                path = calliope.CalliopeConfig.from_env().file_root / 'captures' / sid / f'visual-intent-{draft_id}.png'
                path.parent.mkdir(parents=True, exist_ok=True)
                data = bytes(row['image'])
                extension = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp'}[expert_portraits.image_media_type(data)]
                path = path.with_suffix(extension)
                path.write_bytes(data)
                title = str(source['title']).removeprefix('Capture · ')
                surface = calliope._insert_surfaces(lambda: contextlib.nullcontext(conn), sid, str(source['turn_id']), [{
                    'kind': 'image', 'title': f'Visual intent · {title}', 'tool_name': 'visual_intent',
                    'tool_call_id': f'visual-intent:{draft_id}', 'lineage_key': f'visual-intent:{draft_id}',
                    'parent_surface_id': str(source['id']),
                    'payload': {'storage_path': str(path), 'mime_type': expert_portraits.image_media_type(data),
                                'image_url': public(row)['image_url'], 'model': row['model']},
                    'source': {'origin': 'user_visual_intent', 'author': owner, 'source_surface_id': str(source['id']),
                               'artifact_slug': source.get('artifact_slug') or (source.get('source') or {}).get('artifact_slug'), 'artifact_version': source.get('artifact_version') or (source.get('source') or {}).get('artifact_version'),
                               'original_draft_id': str(history[0]['id']), 'instructions': [h['prompt'] for h in history if h['prompt']]},
                    'presentation': {'purpose': 'visual_intent', 'design_profile': None, 'label': 'Visual reference · not a live screenshot'},
                }])[0]
                conn.execute('UPDATE rvbbit.calliope_surfaces SET design_profile_version_id=NULL WHERE id=%s', (surface['id'],))
                surface['design_profile_version_id'] = None
                conn.execute('UPDATE rvbbit.calliope_visual_intents SET saved_surface_id=%s WHERE id=%s', (surface['id'], draft_id))
        return json_response({'surface': surface}, 201)
