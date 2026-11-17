"""Cache routing for the explicitly configured Hutch endpoint, not arbitrary proxies."""
import hashlib
import json
import os
from urllib.parse import urlsplit


def _endpoint(value):
    try:
        p = urlsplit(str(value or ''))
        if p.scheme not in ('http', 'https') or not p.hostname:
            return None
        return p.scheme, p.hostname.lower(), p.port or (443 if p.scheme == 'https' else 80), p.path.rstrip('/')
    except ValueError:
        return None


def prepare_request(request, params):
    target = _endpoint(os.environ.get('RVBBIT_CLOVER_OPENAI_BASE_URL'))
    if not target or _endpoint(params.get('base_url')) != target:
        return
    # Ordering tool declarations does not change their definitions or availability.
    # Never mutate the shared registry/list; gateway profiles may run concurrently.
    marked = lambda t: any(k in t or k in (t.get('function') or {})
                           for k in ('cache_control', 'prompt_cache_breakpoint'))
    if isinstance(request.get('tools'), list) and not any(marked(t) for t in request['tools']):
        request['tools'] = sorted(request['tools'], key=lambda t: (
            str(t.get('type', '')), str((t.get('function') or {}).get('name') or t.get('name', '')),
        ))
    # Caller-supplied body/header identity takes precedence. In particular, do not
    # add session_id alongside an explicit prompt_cache_key (OpenRouter prefers it).
    body = request.get('extra_body') or {}
    headers = request.get('extra_headers') or {}
    if any(k in request or k in body for k in ('session_id', 'prompt_cache_key')):
        return
    if any(k.lower() in ('x-session-id', 'x-hutch-cache-scope') for k in headers):
        return
    from agent.transports.codex import _cache_scope_from_session_id
    from hermes_constants import get_hermes_home
    scope = _cache_scope_from_session_id(params.get('cache_scope_id') or params.get('session_id'))
    if not scope:
        return  # never collapse unrelated/stateless work into one global bucket
    # Cache lineage survives compression; different profiles and child sessions
    # remain separate. No prompt content, credentials or raw identity is sent.
    key = hashlib.sha256(json.dumps(
        ['calliope-cache-v1', str(get_hermes_home()), scope], separators=(',', ':'),
    ).encode()).hexdigest()
    request['extra_headers'] = {**headers, 'x-hutch-cache-scope': key}
