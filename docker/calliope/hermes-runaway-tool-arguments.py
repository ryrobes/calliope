"""Stop a tool call whose streamed arguments run away.

2026-10-20: asked for a long critique, the model started a
delegate_calliope_specialist call and kept streaming its arguments for 15
minutes (54k-60k chunks per attempt, Calliope run ledger). Every chunk was
real output, so no stale or idle timer could fire, and the chat sat on
"Calliope is writing" until someone pressed Stop. Hermes would then have
retried the cut call with a larger output budget.

Tools whose arguments are a brief (a delegation, a question, SQL) never need
tens of kilobytes. Once one call's arguments pass the ceiling, the stream
attempt ends with RunawayToolArguments; the error classifier treats it as a
non-retryable malformed tool call, so the turn ends with this message and
nothing runs. Content tools (files, patches, dashboards, code) are not
guarded: their size is bounded by the model's output cap and the artifact
limits.

CALLIOPE_BRIEF_TOOL_ARGS_MAX_CHARS sets the ceiling (default 24000; 0 turns
the guard off).
"""
import os

from stage_delivery import canonical_tool

DEFAULT_MAX_CHARS = 24_000
STATE_KEY = '_calliope_tool_argument_chars'

# Tools whose arguments are instructions, not content.
BRIEF_TOOLS = frozenset({
    'delegate_calliope_specialist', 'delegate_all_calliope_specialists', 'continue_calliope_specialist',
    'ask_calliope', 'delegate_task', 'request_calliope_input',
    'run_sql', 'run_workspace_sql', 'recall_calliope_sql', 'search_calliope_actions',
})


class RunawayToolArguments(RuntimeError):
    """A brief-style tool call kept streaming arguments past the ceiling."""


def max_chars():
    raw = os.environ.get('CALLIOPE_BRIEF_TOOL_ARGS_MAX_CHARS', '').strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_MAX_CHARS
    except ValueError:
        return DEFAULT_MAX_CHARS


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _pieces(chunk):
    """(slot, call id, name, argument text) for each tool-call fragment in a
    Chat Completions chunk or an Anthropic stream event."""
    for choice in _field(chunk, 'choices') or []:
        delta = _field(choice, 'delta')
        for call in _field(delta, 'tool_calls') or []:
            function = _field(call, 'function')
            yield (_field(call, 'index', 0) or 0, _field(call, 'id') or '',
                   _field(function, 'name') or '', _field(function, 'arguments') or '')
    kind = _field(chunk, 'type', '')
    if kind == 'content_block_start':
        block = _field(chunk, 'content_block')
        if _field(block, 'type') == 'tool_use':
            yield (_field(chunk, 'index', 0) or 0, _field(block, 'id') or '', _field(block, 'name') or '', '')
    elif kind == 'content_block_delta':
        delta = _field(chunk, 'delta')
        if _field(delta, 'type') == 'input_json_delta':
            yield (_field(chunk, 'index', 0) or 0, '', '', _field(delta, 'partial_json') or '')


def observe(diag, chunk):
    """Count argument characters per tool call in this stream attempt (diag is
    per attempt) and raise RunawayToolArguments past the ceiling."""
    limit = max_chars()
    if not limit or not isinstance(diag, dict):
        return
    calls = diag.setdefault(STATE_KEY, {})
    for slot, call_id, name, text in _pieces(chunk):
        call = calls.get(slot)
        if call is None or (call_id and call.get('id') and call_id != call['id']):
            call = calls[slot] = {'id': call_id, 'name': '', 'chars': 0}
        if call_id:
            call['id'] = call_id
        if name:
            call['name'] = name
        if not isinstance(text, str) or not text:
            continue
        call['chars'] += len(text)
        if call['chars'] > limit and canonical_tool(call['name']) in BRIEF_TOOLS:
            raise RunawayToolArguments(
                f"Stopped a runaway tool call: {canonical_tool(call['name'])} kept writing its arguments past "
                f"{limit:,} characters without finishing, so it was not run. Nothing was changed; "
                "ask again, and keep the request short if it happens twice.")
