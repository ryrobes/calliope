"""Small Python handle to the guest's persistent Cua Driver.

Inside desktop_exec: ``from calliope_computer import cua``. Discover exact
arguments with ``cua.describe('click')``; ordinary Python controls the
loop. No task graph, per-action approval or separate planner is involved.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from contextlib import contextmanager
from computer_quick import ActionNeedsAttention
from computer_browser import browser_page, browser_diagnostics


class DriverError(RuntimeError):
    def __init__(self, tool, result):
        self.result = {'status': 'input_failed', 'tool': tool, 'driver_result': result,
                       'code': result.get('code', 'driver_refused'),
                       'delivery': 'uncertain' if result.get('effect') == 'partial' or 'partial' in str(result.get('code', '')) else 'unconfirmed',
                       'next': result.get('next') or ('Desktop input uses scope=desktop and delivery_mode=foreground without pid/window_id/element tokens. Window input uses scope=window with its target. '
                                if result.get('code') == 'invalid_action_target' else '') +
                               'Correct invalid arguments before retrying. For partial/uncertain delivery, observe the outcome first.'}
        super().__init__(json.dumps(self.result))


class Cua:
    def __init__(self):
        self.schemas = {}
        self.definitions = None
        self.session = os.environ.get("CALLIOPE_CUA_SESSION", os.environ.get("CALLIOPE_COMPUTER_RUN", "callie-computer"))

    def _run(self, *args: str, timeout: int = 90):
        process = subprocess.run(
            ["cua-driver", *args, "--socket",
             os.environ.get("CALLIOPE_CUA_SOCKET", "/tmp/calliope-cua.sock")],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=timeout,
        )
        if process.returncode:
            raise RuntimeError(process.stderr.strip() or process.stdout.strip())
        try:
            return json.loads(process.stdout)
        except json.JSONDecodeError:
            return process.stdout

    def _catalog(self):
        if self.definitions is None:
            export = self._run('dump-docs', '--type', 'mcp', timeout=15)
            if not isinstance(export, dict) or not isinstance(export.get('tools'), list):
                raise RuntimeError('Driver did not return a structured tool catalog')
            self.definitions = {d['name']: d for d in export['tools']}
            self.schemas.update({name: d['input_schema'] for name, d in self.definitions.items()})
        return self.definitions

    def call(self, name: str, arguments: dict | None = None, *, timeout: int = 90):
        if name == 'launch_app':
            from computer_launch import launch
            return launch(self, dict(arguments or {}), timeout)
        return self._call_driver(name, arguments, timeout=timeout)

    def _call_driver(self, name: str, arguments: dict | None = None, *, timeout: int = 90):
        from computer_observation import prepare_capture, normalize_result, unobstructed_capture
        args = dict(arguments or {})
        if name in ('click', 'type_text', 'press_key', 'scroll', 'drag', 'move_cursor') and args.get('scope') == 'desktop':
            args.setdefault('delivery_mode', 'foreground')
        if name == 'press_key':
            key = args.get('key')
            if isinstance(key, str) and len(key) > 1:
                aliases = {'pagedown': 'pagedown', 'pgdn': 'pagedown', 'pgdown': 'pagedown', 'next': 'pagedown',
                           'pageup': 'pageup', 'pgup': 'pageup', 'prior': 'pageup', 'enter': 'Return',
                           'return': 'Return', 'esc': 'Escape', 'escape': 'Escape', 'arrowleft': 'Left',
                           'arrowright': 'Right', 'arrowup': 'Up', 'arrowdown': 'Down', 'spacebar': 'space'}
                args['key'] = aliases.get(re.sub(r'[\s_-]', '', key).casefold(), key)
            if isinstance(args.get('modifiers'), list):
                aliases = {'control': 'ctrl', 'ctl': 'ctrl', 'option': 'alt', 'win': 'super', 'windows': 'super'}
                args['modifiers'] = [aliases.get(m.casefold(), m.casefold()) if isinstance(m, str) else m for m in args['modifiers']]
        if name not in self.schemas:
            self._catalog()
        if name not in self.schemas:
            raise ValueError(f'Unknown driver tool: {name}. Use cua.tools(query=...)')
        schema = self.schemas[name]
        if name == 'get_window_state':
            # Keep the optional raw tree bounded too. Callers can explicitly
            # widen these limits; map/observe skip document content entirely.
            for field, limit in (('max_depth', 5), ('max_elements', 256)):
                if field in schema.get('properties', {}):
                    args.setdefault(field, limit)
        if 'session' in schema.get('properties', {}):
            args.setdefault('session', self.session)
        capture = prepare_capture(schema, args)
        with unobstructed_capture(self, capture or args.get('include_screenshot')):
            result = self._run('call', name, json.dumps(args), timeout=timeout)
        # Normalize before any exception can put an image into a job log.
        result = normalize_result(result, args)
        # Some driver validation failures are plain text despite a zero CLI
        # exit code. Only interpret input-tool error responses, never document
        # text or browser output containing the same words.
        if name in ('click', 'type_text', 'press_key', 'scroll', 'drag', 'move_cursor') and isinstance(result, str):
            if re.match(r'^\s*(?:error:\s*)?unknown (?:key|modifier):', result, re.I):
                raise DriverError(name, {'code': 'invalid_key', 'message': result.strip(),
                                        'next': 'Use cua.describe("press_key") for key names. Page_Down/PgDn and Page_Up/PgUp aliases are supported.'})
        if isinstance(result, dict) and (result.get('isError') or result.get('error') or result.get('ok') is False
                                         or result.get('code') in ('invalid_action_target', 'invalid_arguments', 'stale_target')
                                         or (isinstance(result.get('code'), str) and
                                             set(result) <= {'code', 'message', 'detail', 'details', 'reason', 'suggestion'})
                                         or result.get('refusal') or result.get('status') == 'refused'
                                         or result.get('effect') == 'refused'):
            raise DriverError(name, result)
        return result

    def describe(self, name: str, *, full=False):
        from computer_catalog import HELPERS, describe
        definition = HELPERS.get(name) or self._catalog().get(name)
        if definition is None:
            raise ValueError(f'Unknown tool: {name}. Use cua.tools(query=...)')
        return describe(name, definition, full)

    def tools(self, query=None, *, group=None, full=False):
        from computer_catalog import CORE, DEPRECATED, HELPERS, group as category, summary
        definitions = {**self._catalog(), **HELPERS}
        visible = {n: d for n, d in definitions.items() if full or
                   (n not in DEPRECATED and not d.get('description', '').lstrip().upper().startswith('DEPRECATED'))}
        def entry(name):
            return {'name': name, 'group': category(name), 'summary': summary(name, visible[name])}
        if query or group or full:
            terms = str(query or '').lower().split()
            return [entry(n) for n in sorted(visible) if
                    (not group or category(n) == group) and
                    all(t in (n + ' ' + visible[n].get('description', '') + ' ' + summary(n, visible[n])).lower() for t in terms)]
        return {'start_here': [entry(n) for n in CORE if n in visible],
                'quick_action': entry('act'), 'dialogs': entry('observe'),
                'native_subtask': entry('work'), 'screen_map': entry('map'),
                'groups': {g: sum(category(n) == g for n in visible) for g in sorted({category(n) for n in visible})},
                'more': 'tools(query="export") or tools(group="input"); describe(name); full=True includes advanced/deprecated entries.'}

    def observe(self, *, pid=None, window_id=None, query=None, previous=None, visual_fallback=True):
        from computer_dialogs import observe
        self._catalog()
        return observe(self, pid=pid, window_id=window_id, query=query,
                       previous=previous, visual_fallback=visual_fallback)

    def act(self, *, goal, pid, window_id, query=None, provider='jev', visual=False, expect=None):
        from computer_quick import act
        return act(self, choose, goal=goal, pid=pid, window_id=window_id, query=query,
                   provider=provider, visual=visual, expect=expect)

    def map(self, *, pid=None, window_id=None):
        from computer_work import screen_map, compact_map
        return compact_map(screen_map(self, pid=pid, window_id=window_id))

    def health(self, *, pid, window_id):
        from computer_health import health
        return health(pid=pid, window_id=window_id)

    def work(self, *, goal, pid, window_id, expect=None, max_actions=4, provider='jev'):
        from computer_work import work
        return work(self, choose, goal=goal, pid=pid, window_id=window_id,
                    expect=expect, max_actions=max_actions, provider=provider)


cua = Cua()


@contextmanager
def browser():
    """Attach Playwright to the SAME visible browser; never launch a hidden one.

Preferred path for web page interaction and DOM assertions on this desktop.
Use Cua for native dialogs/browser chrome or when DOM access is unavailable.
The host browser_exec tool is a different browser, not this Computer.
"""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as playwright:
        connection = playwright.chromium.connect_over_cdp("http://127.0.0.1:9223")
        # Install once per document, including future navigations. This only
        # paints real pointer/focus events; no extra agent actions or delays.
        script = Path(__file__).with_name('browser_activity.js').read_text()
        for context in connection.contexts:
            context.add_init_script(script=script)
            for page in context.pages:
                for frame in page.frames:
                    try:
                        frame.evaluate(script)
                    except Exception:
                        pass  # A navigating/detached frame gets the init script next load.
        try:
            yield connection
        finally:
            # Surface a blank WebGL map even when the page catches its own
            # exception and overwrites its status with successful query counts.
            for context in connection.contexts:
                for page in context.pages:
                    try:
                        if page.is_closed() or not page.evaluate("document.visibilityState === 'visible'"):
                            continue
                        report = browser_diagnostics(page)
                        if report['needs_attention']:
                            print('COMPUTER_BROWSER_DIAGNOSTIC ' + json.dumps(report), flush=True)
                    except Exception:
                        pass  # A closed/navigating page must not mask the original error.


def choose(*, goal, observation, options, provider="jev", app="Desktop", screenshot=None, request_id=None):
    """Select one supplied candidate; this does not execute any action.

Use fresh observations. Include a reobserve/abstain candidate when appropriate.
On failure use ordinary reasoning or a fresh observation, never a guessed ID.
``screenshot`` is raw PNG/JPEG bytes for Cua-S1's optional visual adapter.
Returned probabilities are relative to candidates, not task-success scores.
"""
    import base64
    import urllib.error
    import urllib.request
    import uuid
    url = os.environ.get("CALLIOPE_COMPUTER_DECISION_URL")
    token = os.environ.get("CALLIOPE_COMPUTER_DECISION_TOKEN")
    if not url or not token:
        raise RuntimeError("Optional chooser unavailable outside an active desktop job")
    body = {"goal": goal, "observation": observation, "options": options,
            "provider": provider, "app": app, "request_id": request_id or str(uuid.uuid4())}
    if screenshot is not None:
        body["screenshot"] = base64.b64encode(screenshot).decode()
    request = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=190) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Only the appliance's structured error is returned, never credentials.
        raise RuntimeError(exc.read().decode("utf-8", "replace")) from None
