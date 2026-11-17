"""Observe artifact tool generation and completed edits without model calls."""
from collections import OrderedDict
import hashlib
import json
import logging
from pathlib import Path, PurePosixPath
import re
import threading
import time

from artifact_progress import draft_html, public_progress
from stage_delivery import canonical_tool, failed_result

LOG = logging.getLogger(__name__)
WRITERS = {'write_file', 'patch', 'apply_patch'}
PUBLISHERS = {'create_live_app', 'update_live_app', 'publish_dashboard', 'update_dashboard'}
CHECKERS = {'capture_live_app', 'get_live_app'}
IMAGE_TOOLS = {'image_generate', 'generate_image', 'image_edit', 'edit_image'}
LIMIT = 180_000
OBSERVER_LOCK = threading.Lock()


def artifact_paths(path='', patch=''):
    paths = [path] if path else re.findall(r'^\*\*\* (?:Add File|Update File|Move to): ([^\r\n]{1,1024})\r?$', patch, re.M)
    return list(dict.fromkeys(p for p in paths if isinstance(p, str) and
        (PurePosixPath(p).suffix.lower() in {'.html', '.htm', '.svg'} or
         PurePosixPath(p).name == 'app.json' or p.endswith('.app.json'))))[:8]


def partial_string(text, name):
    """Decode only the received prefix of a JSON string, including split escapes."""
    match = re.search(r'"' + re.escape(name) + r'"\s*:\s*"', text)
    if not match:
        return None
    tail, out, i = text[match.end():], [], 0
    while i < len(tail):
        char = tail[i]
        if char == '"':
            break
        if char == '\\':
            if i + 1 >= len(tail):
                break
            end = i + (6 if tail[i + 1] == 'u' else 2)
            if end > len(tail):
                break
            try:
                json.loads('"' + tail[i:end] + '"')
            except ValueError:
                break
            out.append(tail[i:end])
            i = end
        else:
            out.append(char)
            i += 1
    # Decode the entire prefix together so paired Unicode escapes become one
    # character. A trailing half-pair must not break the browser's UTF-8 JSON.
    return json.loads('"' + ''.join(out) + '"').encode('utf-8', errors='ignore').decode('utf-8')


def read_draft(path, task):
    """Bounded read of an explicitly tracked artifact in the tool's own sandbox."""
    from tools import file_tools
    resolved = file_tools._resolve_path_for_task(path, task)
    if (not file_tools._file_ops_uses_host_paths(file_tools._get_file_ops(task))
            or file_tools.get_read_block_error(str(resolved))):
        return None
    resolved = Path(resolved)
    limit = 1_500_000 if resolved.name == 'app.json' or resolved.name.endswith('.app.json') else LIMIT
    if resolved.is_file() and resolved.stat().st_size <= limit:
        with resolved.open(encoding='utf-8', errors='replace') as source:
            return source.read(limit + 1)
    return None


def source_hash(source):
    return hashlib.sha256(source.encode('utf-8', errors='replace')).hexdigest()


def field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class BuildObserver:
    def __init__(self, agent):
        self.agent = agent
        self.lock = threading.RLock()
        self.calls = OrderedDict()
        self.builds = OrderedDict()
        self.active = None
        self.sequence = 0
        self.completed_calls = OrderedDict()

    def send(self, build, phase, force=False, preview=None, preview_kind=None, render_source=None):
        now = time.time()
        if not force and now - build.get('sent', 0) < 1.25:
            return
        build['sent'] = now
        self.sequence += 1
        event = dict(build_id=build['id'], phase=phase, filename=build.get('filename', ''),
                     tool_name=build.get('tool', ''), received_bytes=build.get('bytes', 0),
                     lines=build.get('lines', []), revision=self.sequence, updated_at=now)
        is_app = build.get('filename') == 'app.json' or build.get('filename', '').endswith('.app.json')
        if is_app:
            event['kind'] = 'a2ui'
            if isinstance(render_source, str):
                try: event['render_app'] = json.loads(render_source)
                except ValueError: pass  # Keep the last complete revision.
        elif isinstance(render_source, str):
            event['render_html'] = render_source
        if preview and not is_app:
            event['preview_html'] = preview
            event['preview_kind'] = preview_kind or 'saved'
        callback = getattr(self.agent, 'tool_progress_callback', None)
        if callback:
            callback('artifact.progress', build.get('tool'), progress=public_progress(event))

    def delta(self, key, name='', text=''):
        call = self.calls.setdefault(str(key), {'name': '', 'text': '', 'bytes': 0})
        if name:
            call['name'] = name
        if not isinstance(text, str):
            return
        call['bytes'] += len(text.encode('utf-8'))
        call['text'] = (call['text'] + text)[:LIMIT]
        while len(self.calls) > 24:
            self.calls.popitem(last=False)
        tool = canonical_tool(call['name'])
        # Deferred tool_call arguments contain the real name and input too.
        if tool == 'tool_call':
            match = re.search(r'"name"\s*:\s*"([^"\\]+)"', call['text'])
            tool = canonical_tool(match[1]) if match else tool
        if tool not in WRITERS | PUBLISHERS:
            return
        match = re.search(r'"(?:path|file_path)"\s*:\s*"([^"\\]{1,1024})"', call['text'])
        path = match[1] if match else ''
        paths = artifact_paths(path, call['text'].replace('\\n', '\n') if tool in {'patch', 'apply_patch'} else '')
        if tool in WRITERS and not paths:
            return
        for path in paths if tool in WRITERS else ['']:
            build = self.build(path, tool)
            build['bytes'] = call['bytes']
            if time.time() - build.get('sent', 0) < 1.25:
                continue
            build['lines'] = [min(100, len(line)) for line in call['text'].replace('\\n', '\n').splitlines()[-72:] if line]
            # Only complete HTML prefixes are rendered; partial replacement
            # fragments keep the saved preview and continue the code minimap.
            source = partial_string(call['text'], 'content' if tool == 'write_file' else 'html') if tool == 'write_file' or tool in PUBLISHERS else None
            preview = draft_html(source)
            self.send(build, 'publishing' if tool in PUBLISHERS else 'composing',
                      preview=preview, preview_kind='incoming')

    def build(self, path, tool):
        key = hashlib.sha256(path.encode()).hexdigest()[:20] if path else self.active or 'publication'
        build = self.builds.setdefault(key, {'id': key, 'filename': PurePosixPath(path).name if path else '', 'path': path})
        self.builds.move_to_end(key)
        while len(self.builds) > 8:
            self.builds.popitem(last=False)
        self.active = key
        build['tool'] = tool
        return build

    def tool(self, ref, result=None, started=False):
        if not started:
            key = str(getattr(ref, 'call_id', '') or '')
            if key and key in self.completed_calls:
                return
            if key:
                self.completed_calls[key] = True
                while len(self.completed_calls) > 512:
                    self.completed_calls.popitem(last=False)
        name, args = canonical_tool(ref.name), ref.args if isinstance(ref.args, dict) else {}
        effective_call_id = str(getattr(ref, 'call_id', '') or '')
        if name == 'tool_call':
            inner = args.get('tool_call') if isinstance(args.get('tool_call'), dict) else args
            effective_call_id = str(inner.get('id') or effective_call_id)
            name, args = canonical_tool(inner.get('name')), inner.get('arguments') or {}
        if not isinstance(args, dict):
            return
        if name in IMAGE_TOOLS:
            call_id = effective_call_id
            if not call_id:
                return
            self.sequence += 1
            event = public_progress(dict(
                build_id='image-' + hashlib.sha256(call_id.encode()).hexdigest()[:20],
                kind='image', call_id=call_id, tool_name=name,
                phase='generating' if started else 'failed' if failed_result(result) else 'image_ready',
                prompt=args.get('prompt') or args.get('instruction') or '',
                title=args.get('title') or '', aspect_ratio=args.get('aspect_ratio') or args.get('size') or '',
                revision=self.sequence, updated_at=time.time(),
            ))
            callback = getattr(self.agent, 'tool_progress_callback', None)
            if callback:
                callback('artifact.progress', name, progress=event)
            return
        path = args.get('path') or args.get('file_path') or ''
        if name in WRITERS:
            paths = artifact_paths(path, str(args.get('patch') or ''))
            if not paths:
                return
        elif name not in PUBLISHERS | CHECKERS or not self.active:
            return
        else:
            paths = ['']
        for path in paths:
            build = self.build(path, name)
            phase = 'publishing' if name in PUBLISHERS else 'checking' if name in CHECKERS else 'writing'
            if started:
                self.send(build, phase, force=True)
                continue
            if failed_result(result):
                self.send(build, 'failed', force=True)
                continue
            preview = source = None
            if name in WRITERS:
                task = getattr(ref, 'task_id', None) or getattr(self.agent, 'session_id', 'default')
                build['task'] = task
                source = args.get('content') if name == 'write_file' and not args.get('append') else None
                if source is None:
                    source = read_draft(path, task)
                if isinstance(source, str):
                    build['source_hash'] = source_hash(source)
                    build['lines'] = [min(100, len(line)) for line in source.splitlines()[-72:] if line]
                preview = draft_html(source)
                phase = 'preview'
            self.send(build, phase, force=True, preview=preview, render_source=source)


    def refresh_files(self):
        # Shell/Python edits bypass write_file/patch. Re-read only the small set
        # already attributed to this agent, after a real tool boundary. No scans,
        # mtime-only animation, background model calls or read-cache mutations.
        for build in list(self.builds.values()):
            if not build.get('path') or not build.get('task'):
                continue
            try:
                source = read_draft(build['path'], build['task'])
                if source is None or source_hash(source) == build.get('source_hash'):
                    continue
                build['source_hash'] = source_hash(source)
                build['lines'] = [min(100, len(line)) for line in source.splitlines()[-72:] if line]
                preview = draft_html(source)
                self.send(build, 'preview', force=True, preview=preview, render_source=source)
            except Exception:
                LOG.debug('Tracked draft readback unavailable', exc_info=True)


def observer(agent):
    instance = getattr(agent, '_calliope_build_observer', None)
    if instance is None:
        with OBSERVER_LOCK:
            instance = getattr(agent, '_calliope_build_observer', None)
            if instance is None:
                instance = BuildObserver(agent)
                agent._calliope_build_observer = instance
    return instance


def observe_chunk(agent, chunk):
    """Chat Completions, Anthropic and Responses; hidden reasoning ignored."""
    try:
        obs = observer(agent)
        request = str(getattr(agent, '_current_api_request_id', '') or '')
        with obs.lock:
            choices = field(chunk, 'choices') or []
            for choice in choices:
                delta = field(choice, 'delta')
                for call in field(delta, 'tool_calls', []) or []:
                    fn = field(call, 'function')
                    key = request + ':' + str(field(call, 'index', 0))
                    call_id = field(call, 'id')
                    if call_id and obs.calls.get(key, {}).get('call_id') != call_id:
                        obs.calls[key] = {'name': '', 'text': '', 'bytes': 0, 'call_id': call_id}
                    obs.delta(key, field(fn, 'name', ''), field(fn, 'arguments', ''))
            kind = field(chunk, 'type', '')
            if kind == 'content_block_start':
                block = field(chunk, 'content_block')
                if field(block, 'type') == 'tool_use':
                    key = request + ':' + str(field(chunk, 'index', 0))
                    obs.calls.pop(key, None)
                    obs.delta(key, field(block, 'name', ''))
            elif kind == 'content_block_delta':
                delta = field(chunk, 'delta')
                if field(delta, 'type') == 'input_json_delta':
                    obs.delta(request + ':' + str(field(chunk, 'index', 0)), text=field(delta, 'partial_json', ''))
            elif kind == 'response.output_item.added':
                item = field(chunk, 'item')
                if field(item, 'type') == 'function_call':
                    key = request + ':' + str(field(chunk, 'output_index', 0))
                    obs.calls.pop(key, None)
                    obs.delta(key, field(item, 'name', ''), field(item, 'arguments', ''))
            elif kind == 'response.function_call_arguments.delta':
                obs.delta(request + ':' + str(field(chunk, 'output_index', 0)), text=field(chunk, 'delta', ''))
    except Exception:
        LOG.debug('Build progress unavailable', exc_info=True)


def observe_tool(agent, ref, result=None, started=False):
    try:
        obs = observer(agent)
        with obs.lock:
            obs.tool(ref, result, started)
            if not started:
                obs.refresh_files()
    except Exception:
        LOG.debug('Build preview unavailable', exc_info=True)
