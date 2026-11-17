"""Append-only, price-independent Hermes run telemetry. Never persists bodies/secrets."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
import fcntl
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlsplit
from tool_outcomes import application_failure

HOOKS = {
    'stream.started': 'stream.started', 'stream.completed': 'stream.completed', 'stream.failed': 'stream.failed',
    'pre_api_request': 'model.started', 'post_api_request': 'model.completed',
    'api_request_error': 'model.failed', 'pre_tool_call': 'tool.started',
    'post_tool_call': 'tool.completed', 'on_session_start': 'session.started',
    'on_session_end': 'session.turn_ended', 'on_session_finalize': 'session.finalized',
    'aux.completed': 'aux.completed', 'run.status': 'run.status', 'agent.started': 'agent.started', 'agent.ended': 'agent.ended',
}
_agent = ContextVar('calliope_ledger_agent', default=None)
_attempts = {}
_lock = threading.Lock()
_failures = 0
_STRINGS = ('run_id', 'session_id', 'parent_session_id', 'agent_span_id', 'parent_agent_span_id',
            'task_id', 'turn_id', 'api_request_id', 'tool_call_id', 'tool_name', 'model',
            'provider', 'api_mode', 'platform', 'response_model', 'finish_reason', 'status',
            'error_type', 'turn_exit_reason', 'profile', 'logical_attempt_id', 'stream_attempt_id',
            'provider_request_id', 'response_provider', 'accounting_scope')
_NUMBERS = ('retry_count', 'max_retries', 'status_code', 'api_call_count', 'api_duration',
            'started_at', 'ended_at', 'first_chunk_at', 'duration_ms', 'message_count',
            'tool_count', 'approx_input_tokens', 'request_char_count', 'max_tokens',
            'stream_attempt_no', 'max_stream_attempts', 'received_chunks', 'received_bytes')
_USAGE = ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
          'reasoning_tokens', 'prompt_tokens', 'total_tokens', 'request_count')


def enabled():
    return os.environ.get('CALLIOPE_LEDGER_ENABLED', 'true').lower() not in ('0', 'false', 'off')


def handles_hook(name):
    return enabled() and name in HOOKS


def json_bytes(value):
    """Size of complete SDK JSON representation, NOT wire bytes or truncated hook previews."""
    def sdk_value(item):
        if isinstance(item, SimpleNamespace):
            return vars(item)
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        if hasattr(item, 'model_dump'):
            try:
                return item.model_dump(mode='json', warnings=False)
            except TypeError:
                return item.model_dump()
        raise TypeError('Unsupported SDK value')
    try:
        return len(json.dumps(value, default=sdk_value, ensure_ascii=False,
                              separators=(',', ':'), allow_nan=False).encode('utf-8'))
    except Exception:
        return None


def measured_payload(payload, original):
    result = dict(payload)
    try:
        if isinstance(original, dict) and ('messages' in original or 'input' in original):
            result['_ledger_cache_request'] = cache_request_metadata(original)
        else:
            usage = _get(original, 'usage')
            if usage is not None:
                result['_ledger_cache_usage'] = cache_usage_metadata(usage)
    except (TypeError, ValueError, AttributeError):
        pass  # telemetry must never prevent a request
    # SDK controls/headers aren't request body. No values are persisted.
    if isinstance(original, dict):
        original = {k: v for k, v in original.items()
                    if k not in ('timeout', 'http_client', 'extra_headers')}
    result['_ledger_json_bytes'] = json_bytes(original)
    request_id = _get(original, 'id')
    # Hermes fabricates stream-* IDs when the provider supplied no response ID.
    if isinstance(request_id, str) and not request_id.startswith('stream-'):
        result['_ledger_provider_request_id'] = request_id[:256]
    upstream = _get(original, 'provider')
    if isinstance(upstream, str):
        result['_ledger_response_provider'] = upstream[:256]
    usage = _get(original, 'usage')
    cost = _get(usage, 'cost')
    if _scalar(cost) and cost >= 0:
        result['_ledger_reported_cost_usd'] = cost
    return result


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def cache_request_metadata(request):
    body = {**request, **(request.get('extra_body') or {})}
    headers = {k.lower(): v for k, v in (request.get('extra_headers') or {}).items()}
    identity = next(((name, value) for name, value in (
        ('session_id', body.get('session_id')), ('x-session-id', headers.get('x-session-id')),
        ('prompt_cache_key', body.get('prompt_cache_key')),
        ('hutch_scope', headers.get('x-hutch-cache-scope')),
    ) if isinstance(value, str) and value), ('none', None))
    messages = body.get('messages') or body.get('input') or []
    prefix = []
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict) or message.get('role') not in ('system', 'developer'):
                break
            prefix.append(message)
    return {
        'identity_source': identity[0],
        'identity_sha256': _digest(identity[1]) if identity[1] else None,
        'tools_sha256': _digest(body.get('tools') or []),
        'system_prefix_sha256': _digest([body.get('instructions'), prefix]),
        'system_message_count': len(prefix),
        'tool_count': len(body.get('tools') or []),
        'basis': 'canonical_sdk_json_hashes_not_wire_tokens',
    }


def cache_usage_metadata(usage):
    """Preserve absent counters as unknown, rather than canonical default zero."""
    def count(*paths):
        for path in paths:
            value = usage
            for key in path:
                value = _get(value, key)
            if _scalar(value) and value >= 0:
                return value
        return None
    read = count(('prompt_tokens_details', 'cached_tokens'), ('input_tokens_details', 'cached_tokens'),
                 ('cache_read_input_tokens',), ('prompt_cache_hit_tokens',), ('cached_tokens',))
    write = count(('prompt_tokens_details', 'cache_write_tokens'), ('input_tokens_details', 'cache_write_tokens'),
                  ('prompt_tokens_details', 'cache_creation_input_tokens'), ('input_tokens_details', 'cache_creation_tokens'),
                  ('cache_creation_input_tokens',), ('cache_write_tokens',))
    prompt = count(('prompt_tokens',), ('input_tokens',))
    if _get(usage, 'prompt_tokens') is None and (
            _get(usage, 'cache_read_input_tokens') is not None or _get(usage, 'cache_creation_input_tokens') is not None):
        prompt = prompt + (read or 0) + (write or 0) if prompt is not None else None
    return {'prompt_tokens': prompt, 'cache_read_tokens': read, 'cache_write_tokens': write,
            'basis': 'provider_reported_counters'}


def _scalar(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def observe(name, **data):
    """Fail open for application work; report write failures without logging payloads."""
    global _failures
    if not handles_hook(name):
        return
    try:
        _record(name, data)
    except Exception as exc:
        with _lock:
            _failures += 1
        logging.getLogger(__name__).error('Calliope ledger write failed (%s)', type(exc).__name__)


def _record(name, data):
    from gateway.calliope_run_context import current_run_id
    now = time.time()
    context = _agent.get() or {}
    merged = {**context, **data}
    merged['run_id'] = data.get('run_id') or current_run_id() or context.get('run_id')
    if not merged.get('profile'):
        try:
            from hermes_constants import get_hermes_home
            home = Path(get_hermes_home())
            merged['profile'] = home.name if home.parent.name == 'profiles' else 'default'
        except ImportError:
            pass
    row = {'schema_version': 1, 'event_id': str(uuid.uuid4()), 'event': HOOKS[name],
           'ts': datetime.fromtimestamp(now, timezone.utc).isoformat(), 'epoch_seconds': now,
           'source': 'hermes', 'pid': os.getpid(), 'cost_usd': None,
           'source_revision': os.environ.get('CALLIOPE_LEDGER_REVISION', 'unknown'),
           'billing_mode': 'unknown',
           'cost_status': 'unpriced', 'write_failures_in_process': _failures}
    for key in _STRINGS:
        value = merged.get(key)
        if isinstance(value, str):
            row[key] = value[:512]
    for key in _NUMBERS:
        value = merged.get(key)
        if _scalar(value):
            row[key] = value
    for key in ('completed', 'failed', 'interrupted', 'retryable'):
        if isinstance(data.get(key), bool):
            row[key] = data[key]
    # Never retain URL credentials, queries, paths (which can carry keys), or error messages.
    if isinstance(data.get('base_url'), str):
        url = urlsplit(data['base_url'])
        row['endpoint_host'] = url.hostname
        row['endpoint_scheme'] = url.scheme
    error = data.get('error')
    if isinstance(error, dict) and isinstance(error.get('type'), str):
        row['error_type'] = error['type'][:128]
    if name.startswith(('pre_api', 'post_api', 'api_request', 'aux.')):
        key = (row.get('agent_span_id'), row.get('session_id'), row.get('api_request_id'))
        with _lock:
            if name == 'pre_api_request':
                _attempts[key] = (str(uuid.uuid4()), time.monotonic())
                active = _attempts[key]
            elif name == 'aux.completed':
                active = None  # Auxiliary work must not consume a main request's attempt.
            else:
                active = _attempts.pop(key, None)
        row['attempt_id'] = active[0] if active else None
        if active and name != 'pre_api_request':
            row['attempt_elapsed_ms'] = round((time.monotonic() - active[1]) * 1000, 3)
        payload_key = 'response' if name in ('post_api_request', 'aux.completed') else 'request'
        payload = data.get(payload_key)
        size = payload.get('_ledger_json_bytes') if isinstance(payload, dict) else None
        row[payload_key + '_json_bytes'] = size if _scalar(size) else None
        if name == 'aux.completed' and isinstance(data.get('request'), dict):
            request_size = data['request'].get('_ledger_json_bytes')
            row['request_json_bytes'] = request_size if _scalar(request_size) else None
        row['size_basis'] = 'sdk_json_representation_before_hook_truncation_not_wire'
        if isinstance(payload, dict) and isinstance(payload.get('_ledger_provider_request_id'), str):
            row['provider_request_id'] = payload['_ledger_provider_request_id']
        if isinstance(payload, dict) and isinstance(payload.get('_ledger_response_provider'), str):
            row['response_provider'] = payload['_ledger_response_provider']
        if isinstance(payload, dict) and _scalar(payload.get('_ledger_reported_cost_usd')):
            row['reported_cost_usd'] = payload['_ledger_reported_cost_usd']
        if isinstance(payload, dict):
            for key in ('cache_request', 'cache_usage'):
                if isinstance(payload.get('_ledger_' + key), dict):
                    row[key] = payload['_ledger_' + key]
        usage = data.get('usage')
        row['usage'] = ({k: usage[k] for k in _USAGE if _scalar(usage.get(k)) and usage[k] >= 0}
                        if isinstance(usage, dict) else None)
        row['usage_basis'] = 'hermes_canonical_v0214' if row['usage'] is not None else 'unavailable'
    if name.startswith('stream.'):
        row['size_basis'] = 'hermes_stream_delta_estimate_not_wire_bytes'
    if name in ('pre_tool_call', 'post_tool_call'):
        row['arguments_json_bytes'] = json_bytes(data.get('args'))
        if name == 'post_tool_call':
            row['result_json_bytes'] = json_bytes(data.get('result'))
            row['transport_status'] = row.get('status')
            failure = application_failure(data.get('result'))
            row['application_status'] = 'error' if failure else 'ok' if row.get('status') in ('ok','success') else 'unknown'
            if failure:
                row['status'] = 'error'
                row['error_type'] = failure
    root = Path(os.environ.get('CALLIOPE_LEDGER_DIR', '/opt/data/calliope-ledger'))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / (datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d') + '.jsonl')
    raw = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with os.fdopen(fd, 'ab', closefd=False) as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(fd)
    finally:
        os.close(fd)


def agent_span(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        if not enabled():
            return function(self, *args, **kwargs)
        parent = _agent.get() or {}
        context = {'agent_span_id': str(uuid.uuid4()), 'parent_agent_span_id': parent.get('agent_span_id', ''),
                   'session_id': str(getattr(self, 'session_id', '') or ''),
                   'parent_session_id': str(getattr(self, '_parent_session_id', '') or ''),
                   'model': str(getattr(self, 'model', '') or ''),
                   'provider': str(getattr(self, 'provider', '') or ''),
                   'platform': str(getattr(self, 'platform', '') or '')}
        token = _agent.set(context)
        started = time.monotonic()
        observe('agent.started')
        try:
            result = function(self, *args, **kwargs)
            flags = result if isinstance(result, dict) else {}
            status = 'interrupted' if flags.get('interrupted') else 'failed' if flags.get('failed') else 'completed'
            observe('agent.ended', status=status, duration_ms=(time.monotonic()-started)*1000)
            return result
        except BaseException as exc:
            observe('agent.ended', status='interrupted' if isinstance(exc, (KeyboardInterrupt, InterruptedError))
                    or type(exc).__name__ == 'CancelledError' else 'failed', error_type=type(exc).__name__,
                    duration_ms=(time.monotonic()-started)*1000)
            raise
        finally:
            with _lock:
                for key in list(_attempts):
                    if key[0] == context['agent_span_id']:
                        _attempts.pop(key, None)
            _agent.reset(token)
    return wrapped


def _best_effort(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except Exception:
            # Diagnostics must never change inference success or retry behavior.
            global _failures
            _failures += 1
            return {}
    return wrapped


@_best_effort
def stream_attempt_started(agent, number, maximum):
    """A transport attempt within a main-loop request; never a second billed call."""
    if not enabled(): return {}
    context = _agent.get() or {}
    session = str(getattr(agent, 'session_id', '') or '')
    request = getattr(agent, '_current_api_request_id', None)
    with _lock:
        logical = _attempts.get((context.get('agent_span_id'), session, request))
    data = {'session_id': session, 'api_request_id': request,
            'logical_attempt_id': logical[0] if logical else None,
            'stream_attempt_id': str(uuid.uuid4()), 'stream_attempt_no': number,
            'max_stream_attempts': maximum, 'model': str(getattr(agent, 'model', '') or ''),
            'accounting_scope': 'stream_diagnostic', '_started': time.monotonic()}
    observe('stream.started', **data)
    return data


@_best_effort
def stream_chunk_metadata(diag, chunk):
    """Capture only provider identity; never persist deltas, arguments or reasoning."""
    if not enabled() or not isinstance(diag, dict): return
    for source, target in [('id', 'provider_request_id'), ('model', 'response_model'), ('provider', 'response_provider')]:
        value = chunk.get(source) if isinstance(chunk, dict) else getattr(chunk, source, None)
        if isinstance(value, str) and value and not value.startswith('stream-'):
            diag['_calliope_' + target] = value[:256]


@_best_effort
def stream_attempt_finished(context, diag, error=None, response=None):
    if not context: return
    diag = diag if isinstance(diag, dict) else {}
    if response is not None:
        stream_chunk_metadata(diag, response)
    data = dict(context)
    data['duration_ms'] = (time.monotonic() - data.pop('_started')) * 1000
    for key in ('provider_request_id', 'response_model', 'response_provider'):
        data[key] = diag.get('_calliope_' + key)
    data.update(received_chunks=diag.get('chunks'), received_bytes=diag.get('bytes'),
                first_chunk_at=diag.get('first_chunk_at'), status_code=diag.get('http_status'))
    if error is not None:
        data['error_type'] = type(error).__name__
    observe('stream.failed' if error is not None else 'stream.completed', **data)
