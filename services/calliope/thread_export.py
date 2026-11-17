"""Chronological Markdown from the authorized chat/Stage projection, without inference."""
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
import html
import json
import re

UTC = timezone.utc
FILE_LIMIT = 1024 * 1024
DOCUMENT_BUDGET = 8 * FILE_LIMIT


def timestamp(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def clock(value):
    dt = timestamp(value)
    return dt.isoformat(timespec='milliseconds').replace('+00:00', 'Z') if dt else 'Timestamp unavailable'


def inline(value):
    return re.sub(r'([\\`*_{}\[\]<>#!|])', r'\\\1', ' '.join(str(value or '').split()))


def fence(value, language='json'):
    text = value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False, default=str)
    marker = '`' * max(3, 1 + max((len(x) for x in re.findall(r'`+', text)), default=0))
    return f'{marker}{language}\n{text}\n{marker}'


def link(label, value, base):
    value = str(value or '').strip()
    if not value:
        return ''
    url = urljoin(base.rstrip('/') + '/', value)
    if urlsplit(url).scheme not in ('http', 'https'):
        return ''
    return f'[{inline(label)}](<{quote(url, safe=":/?=&%#@+~,;!$*[]-_.")}>)'


def text_documents(conn_factory, session_id, surfaces, file_root):
    """Include bounded stored text; never fetch remote URLs or read arbitrary paths."""
    ids = [s['id'] for s in surfaces if s.get('kind') == 'document']
    if not ids:
        return {}
    with conn_factory() as conn:
        rows = conn.execute('SELECT id,payload FROM rvbbit.calliope_surfaces '
                            'WHERE session_id=%s::uuid AND id=ANY(%s::uuid[]) AND kind=\'document\'',
                            (session_id, ids)).fetchall()
    result = {}
    remaining = DOCUMENT_BUDGET
    root = (Path(file_root) / 'files').resolve()
    for row in rows:
        payload = row.get('payload') or {}
        name = str(payload.get('original_name') or payload.get('filename') or '')
        suffix = Path(name).suffix.lower()
        mime = str(payload.get('mime_type') or '').split(';')[0]
        if suffix not in ('.md', '.markdown', '.txt', '.csv', '.json') and mime not in (
                'text/markdown', 'text/plain', 'text/csv', 'application/json'):
            continue
        key = str(row['id'])
        language = 'markdown' if suffix in ('.md', '.markdown') or mime == 'text/markdown' else (
            'json' if suffix == '.json' or mime == 'application/json' else 'csv' if suffix == '.csv' else 'text')
        try:
            path = Path(payload.get('storage_path') or '').resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise OSError('Not a stored export file')
            if remaining <= 0:
                result[key] = {'note': 'Text attachment budget reached; use the download link for the complete file.'}
                continue
            limit = min(FILE_LIMIT, remaining)
            with path.open('rb') as f:
                raw = f.read(limit + 1)
            clipped = len(raw) > limit
            raw = raw[:limit]
            remaining -= len(raw)
            result[key] = {'text': raw.decode('utf-8', errors='replace'), 'language': language,
                           'note': 'Text attachment shortened; use the download link for the complete file.' if clipped else ''}
        except (OSError, RuntimeError, ValueError):
            result[key] = {'note': 'Stored text was unavailable at export time; the file link is retained.'}
    return result


def table(payload):
    rows = payload.get('rows')
    if not isinstance(rows, list):
        return ''
    columns = payload.get('columns') or []
    columns = [str(c.get('name') or c.get('field') or '') if isinstance(c, dict) else str(c) for c in columns]
    if not columns and rows:
        columns = list(dict.fromkeys(k for r in rows if isinstance(r, dict) for k in r))
        if not columns and isinstance(rows[0], list):
            columns = [f'Column {n + 1}' for n in range(len(rows[0]))]
    if not columns:
        return '_No saved rows._'
    def cell(value):
        if value is None:
            return 'NULL'
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, default=str)
        return html.escape(str(value)).replace('\\', '\\\\').replace('|', '\\|').replace('`', '\\`').replace('\r\n', '\n').replace('\n', '<br>')
    output = ['| ' + ' | '.join(cell(c) for c in columns) + ' |', '| ' + ' | '.join('---' for _ in columns) + ' |']
    for row in rows:
        values = [row.get(c) for c in columns] if isinstance(row, dict) else list(row) if isinstance(row, (list, tuple)) else [row]
        output.append('| ' + ' | '.join(cell(values[i] if i < len(values) else None) for i in range(len(columns))) + ' |')
    if payload.get('truncated') or (isinstance(payload.get('row_count'), int) and payload['row_count'] > len(rows)):
        output.append(f'\n_Saved preview: {len(rows)} rows. The original result was larger; the query was not rerun._')
    return '\n'.join(output)


def stage_body(surface, session_id, base, document):
    payload = dict(surface.get('payload') or {})
    kind = surface.get('kind') or 'item'
    parts = [link('Open stage item', f'/calliope?session={quote(session_id)}&surface={quote(str(surface.get("id") or ""))}', base)]
    if surface.get('artifact_slug'):
        parts.append(link('Published artifact', '/apps/' + quote(str(surface['artifact_slug']), safe=''), base))
    for key, label in [('url', 'Open'), ('open_url', 'Open'), ('download_url', 'Download file'), ('image_url', 'Image'), ('base_image_url', 'Original image'), ('overlay_image_url', 'Markup')]:
        url = payload.pop(key, None)
        if url:
            rendered = link(label, url, base)
            if rendered:
                parts.append('!' + rendered if key == 'image_url' else rendered)
    for key in ['answer', 'caveat', 'markdown', 'text', 'body', 'description', 'summary']:
        value = payload.get(key)
        if isinstance(value, str):
            payload.pop(key)
            if value.strip():
                parts.append(('**Caveat:** ' if key == 'caveat' else '') + value.strip())
    source = surface.get('source') or {}
    sql = payload.pop('sql', None) or source.get('sql')
    if sql:
        parts.append(fence(str(sql), 'sql'))
    if isinstance(payload.get('rows'), list):
        parts.append(table(payload))
        payload.pop('rows', None)
        payload.pop('columns', None)
    if document:
        if 'text' in document:
            parts.append(document['text'] if document['language'] == 'markdown' else fence(document['text'], document['language']))
        if document.get('note'):
            parts.append('_' + document['note'] + '_')
    if kind in ('chart', 'map') and payload.get('queries'):
        parts.append('_Live visualization: saved specification and query bindings below; no queries were rerun for this export._')
    # Keep saved specifications, evidence, decisions and receipts inspectable,
    # but omit renderer plumbing and server-only paths, not user-facing data.
    for key in ['runtime', 'storage_path', 'source_path', 'path', 'content_sha256']:
        payload.pop(key, None)
    if payload:
        parts.append('### Saved stage data\n\n' + fence(payload))
    return '\n\n'.join(p for p in parts if p)


def render(data, base_url, *, progress=(), documents=None, exported_at=None):
    session = data['session']
    session_id = str(session['id'])
    presentation = data.get('presentation') or {}
    title = str(presentation.get('title') or session.get('title') or 'Calliope thread')
    agent = str(session.get('specialist_name') or session.get('specialist_key') or 'Calliope')
    turns = data.get('turns') or []
    events = []
    documents = documents or {}
    # Ordering is by recorded event time, never by Stage packing or turn number.
    def add(at, category, label, body, metadata=''):
        if not body:
            return
        events.append((timestamp(at) or datetime.max.replace(tzinfo=UTC), category, len(events),
                       f'## {clock(at)} — {inline(label)}\n\n' + (metadata + '\n\n' if metadata else '') + body.strip()))
    progress_by_turn = {}
    for item in progress:
        progress_by_turn.setdefault(str(item['turn_id']), []).append(item)
    for turn in turns:
        turn_id = str(turn['id'])
        ordinal = turn.get('ordinal', '?')
        meta = f'Turn {ordinal} · `{turn_id}`'
        author = turn.get('author') or session.get('owner') or {}
        name = str(author.get('display_name') or author.get('email') or session.get('owner_email') or 'User')
        body = str(turn.get('user_message') or '').strip()
        attachments = [link(a.get('name') or 'Attachment', a.get('url'), base_url) for a in turn.get('attachments') or []]
        refs = {key: turn[key] for key in ['evidence_refs', 'object_refs'] if turn.get(key)}
        if attachments:
            body += '\n\n' + '\n\n'.join(a for a in attachments if a)
        if refs:
            body += '\n\n### Attached references\n\n' + fence(refs)
        turn_kind = turn.get('turn_kind') or 'chat'
        label = 'Team update → ' + agent if turn_kind == 'agent_message' else name if turn_kind == 'chat' else turn_kind.replace('_', ' ').title()
        add(turn.get('created_at'), 0, label, body, meta)
        saved_progress = progress_by_turn.get(turn_id, [])
        seen = set()
        for item in saved_progress:
            text = str(item.get('text') or '').strip()
            normalized = ' '.join(text.split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            add(item.get('created_at'), 1, agent + ' · progress', text, meta)
        if not saved_progress:
            for text in turn.get('working_updates') or []:
                add(None, 1, agent + ' · earlier progress', str(text), meta + ' · historical timestamp unavailable')
        at = turn.get('completed_at') or turn.get('heartbeat_at') or turn.get('created_at')
        status = turn.get('status') or 'unknown'
        completion = meta + ' · ' + inline(status)
        if not turn.get('completed_at'):
            completion += ' · time is the last saved turn activity'
        add(at, 3, agent, str(turn.get('assistant_message') or ''), completion)
        receipt = dict(turn.get('response_receipt') or {})
        # Voice is a presentation rewrite. The original messages are above.
        receipt.pop('voice', None)
        receipt.pop('presentation', None)
        if receipt:
            add(at, 4, 'Receipt', fence(receipt), completion)
        if turn.get('error'):
            add(at, 4, 'Turn error', fence(str(turn['error']), 'text'), completion)
    for surface in data.get('surfaces') or []:
        meta = f'Stage · {inline(surface.get("kind") or "item")} · `{surface.get("id")}`'
        if surface.get('turn_id'):
            meta += f' · turn `{surface["turn_id"]}`'
        if surface.get('artifact_version') is not None:
            meta += f' · version {surface["artifact_version"]}'
        if surface.get('updated_at') and surface.get('updated_at') != surface.get('created_at'):
            meta += f' · latest saved update {clock(surface["updated_at"])}'
        add(surface.get('created_at'), 2, 'Stage: ' + str(surface.get('title') or surface.get('kind') or 'Item'),
            stage_body(surface, session_id, base_url, documents.get(str(surface.get('id')))), meta)
    page = data.get('page') or {}
    revision = page.get('revision') or {}
    if revision.get('body'):
        add(revision.get('created_at') or page.get('last_refreshed_at'), 2,
            'Page: ' + str(page.get('title') or 'Living page'), str(revision['body']),
            f'Pinned page · version {revision.get("version", "?")} · latest accessible revision')
    header = [f'# {inline(title)}', link('Open conversation', f'/calliope?session={quote(session_id)}', base_url),
              f'- Session: `{session_id}`\n- Created: {clock(session.get("created_at"))}\n- Exported: {clock(exported_at or datetime.now(UTC))}\n- Timeline: oldest first · UTC\n- Saved turns: {len(turns)} · Stage postings: {len(data.get("surfaces") or [])}',
              'This export includes saved messages, earlier progress, receipts, and accessible stage postings, including older revisions. Original assistant text is used rather than TTS rewrites. Images and published pages remain links and may require sign-in. Live data is not refreshed; a running session may have more content after this export.']
    if presentation.get('summary'):
        header.append('## Thread summary\n\n' + str(presentation['summary']))
    stem = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')[:80] or 'calliope-thread'
    timeline = [item[3] for item in sorted(events, key=lambda item: item[:3])]
    return '\n\n'.join(header) + '\n\n---\n\n' + '\n\n---\n\n'.join(timeline) + '\n', f'{stem}-{session_id[:8]}.md'
