"""Small agent-facing index over the installed driver's structured schema export."""
from copy import deepcopy
import re

CORE = ('list_windows', 'list_apps', 'launch_app', 'get_window_state', 'click',
        'type_text', 'verify_state', 'browser')
DEPRECATED = {'page', 'escalate_session', 'get_session_state'}
SUMMARIES = {
    'press_key': 'Press a named key with separate modifiers; desktop scope uses foreground delivery.',
    'health': 'Check whether the application event loop responds; report process state and memory without closing it.',
    'map': 'Read a compact native screen map: active dialog, grouped controls and Calc commands; skips document cells.',
    'work': 'Run a bounded native subtask using the screen map and Jev; return progress, verified outcome or an exception for Callie.',
    'list_windows': 'Find native windows and their exact pid/window_id.',
    'list_apps': 'Find installed applications and launch targets.',
    'launch_app': 'Open a persistent application and resolve its surviving window pid/window_id.',
    'get_window_state': 'Read exposed controls and fresh element tokens; text only by default.',
    'click': 'Click an observed control using its fresh element_token.',
    'type_text': 'Type into an observed field using its fresh element_token.',
    'verify_state': 'Check explicit window/control predicates after an action.',
    'browser': 'Attach Playwright to the same visible Chrome; preferred for web pages.',
    'observe': 'Read the foreground dialog, controls and screen coordinates; localized OCR fills accessibility gaps.',
    'act': 'Choose and click one observed native control, then check an explicit expectation.',
}
WARNINGS = {
    'health': ['Read-only liveness check, not document verification. Unsupported ping means unknown, not unresponsive.',
               'On an unresponsive app, preserve unsaved work or use an already-authorized restart; do not keep trying input backends.'],
    'map': ['Read-only. Incomplete accessibility and OCR are identified; OCR text alone is not an executable button.',
            'Only the active dialog is actionable while it blocks the document. Content cells are not enumerated.'],
    'work': ['Prefer this for Calc menus and native dialog chains instead of repeatedly reading the full window tree.',
             'A failed accessibility read is observation_unavailable, not a screen change. One read retry and an event-loop health check are automatic.',
             'On application_unresponsive or repeated no_observed_change, recover the application or report the blocker; do not debug X11 during the task.',
             'One concrete subtask, at most 6 inputs. Stops on no change, stale targets or ambiguity; no automatic retry.',
             'Exact unique label goals bypass Jev. Otherwise it selects a region and then an observed action.',
             'Only explicit expect predicates produce verified=true. progress means observed UI change, including navigation that now needs caller text or verification.',
             'Selecting an editable field returns handoff=caller_input once it is focused; an already-focused field needs no click. Type explicit paths/values directly, then reobserve; no values are invented.',
             'Return to Callie on needs_attention with the screen/steps evidence; do not repeat the same failed call.'],
    'launch_app': ['Close office documents gracefully before external edits/reopening. Killing soffice can create recovery dialogs and lose unsaved changes.',
                   'Use this for persistent GUI apps. Shell &, nohup and Popen remain job children.',
                   'Use returned pid/window_id, not launcher_pid. Single-instance apps can hand off to an existing process.',
                   'window_resolution reports ready, multiple_windows or unresolved. Check windows when there is no unique target.'],
    'get_window_state': [
        'Prefer map()/observe() for native controls. This lower-level driver tree defaults to max_depth=5, max_elements=256. Explicit limits override these defaults; deeper reads can expand expensive virtual document content.',
        'Harness default: include_screenshot=false. Explicit screenshots are saved to guest files, never base64 logs.',
        'If a dialog blocks the window or this tree is sparse, use cua.observe(pid=..., window_id=...) for related dialogs and localized OCR.',
        'The tree describes exposed controls, not all visible content. Empty matches do not prove absence.',
        'Use browser() for page content; read shell output for command results; look_at_desktop for visual gaps.'],
    'click': ['Use a fresh element_token; element_index also needs its matching snapshot_id.',
              'Desktop scope uses screen coordinates without pid/window_id/element tokens. Foreground delivery defaults on for desktop input.',
              'Coordinates must come from a fresh screenshot. Delivery is not proof of success.'],
    'type_text': ['Use a fresh element_token or grounded field coordinates. Verify the resulting value.'],
    'press_key': ['Use key="s", modifiers=["ctrl"], not key="Ctrl+S".',
                  'Desktop scope omits pid/window_id/element tokens and defaults to foreground delivery; window scope retains its target.',
                  'Common keys: Return, Escape, Tab, BackSpace, Delete, Insert, Home, End, Left, Right, Up, Down, pageup, pagedown, F1-F12.',
                  'Aliases include Enter, Esc, Page_Down, Page_Up, PgDn, PgUp and ArrowLeft/Right/Up/Down. Unknown keys raise DriverError.'],
    'verify_state': ['Pass explicit expect predicates. Incomplete trees cannot prove element absence.'],
    'browser': ['Uses the desktop Chrome/profile, not the separate host browser_exec environment.',
                'Use browser_page(b, url) to navigate in the existing authenticated context; never create a fresh context or bypass TLS to fix login.',
                'For blank maps or repeated missed clicks, print(browser_diagnostics(page)) before further input. A canvas element does not prove the map rendered.',
                'Use Cua for native dialogs and browser chrome, or when DOM access is unavailable.'],
    'observe': ['Reads only: bounded native surface and dialogs, without a full driver-tree fallback. Document cells are not traversed; OCR text has unproven clickability.',
                'controls[].click_arguments use fresh native tokens or desktop pixel coordinates. Do not rescale OCR coordinates.',
                'Pass previous=the_prior_observation after an action: a new confirmation is progress, not a failed click.'],
    'act': ['One click, not a task planner. Use direct click for a known exact target.',
            'Raises ActionNeedsAttention on ambiguity, drift, chooser failure or unmet expectations. Do not blindly retry.',
            'Scores rank supplied options; only explicit expect predicates can produce verified=true.'],
}
EXAMPLES = {
    'list_windows': {'on_screen_only': True}, 'list_apps': {},
    'launch_app': {'name': 'libreoffice', 'additional_arguments': ['--writer', '/home/kasm-user/note.odt']},
    'get_window_state': {'pid': 123, 'window_id': 456, 'query': 'Export'},
    'click': {'pid': 123, 'window_id': 456, 'element_token': 's00000001:2'},
    'type_text': {'pid': 123, 'window_id': 456, 'element_token': 's00000001:3', 'text': 'Quarterly report'},
    'press_key': {'scope': 'desktop', 'key': 'Page_Down', 'modifiers': ['ctrl']},
    'verify_state': {'pid': 123, 'window_id': 456, 'expect': [{'element': {'selector': {'label_contains': 'Saved'}, 'exists': True}}]},
}
HELPERS = {
    'health': {'input_schema': {'type': 'object', 'required': ['pid', 'window_id'], 'properties': {
        'pid': {'type': 'integer'}, 'window_id': {'type': 'integer'}}},
        'example': 'print(cua.health(pid=123, window_id=456))'},
    'map': {'input_schema': {'type': 'object', 'properties': {
        'pid': {'type': ['integer', 'null']}, 'window_id': {'type': ['integer', 'null']}}},
        'example': 'print(cua.map(pid=123, window_id=456))'},
    'work': {'input_schema': {'type': 'object', 'required': ['goal', 'pid', 'window_id'], 'properties': {
        'goal': {'type': 'string', 'maxLength': 3500}, 'pid': {'type': 'integer'}, 'window_id': {'type': 'integer'},
        'expect': {'type': ['array', 'null'], 'description': 'Explicit verify_state outcome predicates.'},
        'max_actions': {'type': 'integer', 'minimum': 1, 'maximum': 6, 'default': 4},
        'provider': {'enum': ['jev', 'cua-s1'], 'default': 'jev'}}},
        'example': "print(cua.work(goal='Keep the Excel file format and finish the save confirmation', pid=123, window_id=456))\n# Inspect status/steps. progress is not proof the workbook was saved; check the file/document outcome."},
    'observe': {'input_schema': {'type': 'object', 'properties': {
        'pid': {'type': ['integer', 'null']}, 'window_id': {'type': ['integer', 'null']},
        'query': {'type': ['string', 'null']}, 'previous': {'type': ['object', 'null']},
        'visual_fallback': {'type': 'boolean', 'default': True}}},
        'example': 'state = cua.observe(pid=123, window_id=456)\nprint(state)\n# Choose an exact observed control and check its current question.\nbutton = next(c for c in state["controls"] if c["label"] == "Yes")\ncua.call("click", button["click_arguments"])\nprint(cua.observe(pid=123, window_id=456, previous=state))'},
    'browser': {'description': 'Use browser_page(b, url) to open or reuse a visible tab without resetting its state. Pass expected_version from the publication receipt after an update: only stale documents reload. Use reload=True to recover a page failure. Wait for app-specific data and rendered features before asserting.',
                'input_schema': {'type': 'object', 'properties': {}},
                'example': "from calliope_computer import browser, browser_page, browser_diagnostics\nwith browser() as b:\n    page = browser_page(b, url)\n    print(browser_diagnostics(page))\n    page.get_by_role('button', name='Export', exact=True).click()\n    assert page.get_by_text('Export complete', exact=True).is_visible()"},
    'act': {'input_schema': {'type': 'object', 'required': ['goal', 'pid', 'window_id'], 'properties': {
        'goal': {'type': 'string'}, 'pid': {'type': 'integer'}, 'window_id': {'type': 'integer'},
        'query': {'type': ['string', 'null']}, 'provider': {'enum': ['jev', 'cua-s1'], 'default': 'jev'},
        'visual': {'type': 'boolean', 'default': False},
        'expect': {'type': ['array', 'null'], 'description': 'verify_state predicates for the observed outcome.'}}},
        'example': "print(cua.act(goal='Export the current report', pid=123, window_id=456, query='Export', expect=[{'element': {'selector': {'label_contains': 'Export complete'}, 'exists': True}}]))"},
}


def group(name):
    if name in HELPERS: return 'helpers'
    if name.startswith('browser_') or name == 'get_browser_state': return 'browser_advanced'
    if name in ('list_apps', 'launch_app', 'list_windows', 'bring_to_front', 'close_window'): return 'apps_windows'
    if name.startswith(('get_', 'verify_', 'inspect_')): return 'observe'
    if name in ('click', 'type_text', 'press_key', 'scroll', 'drag', 'move_cursor'): return 'input'
    if any(word in name for word in ('screenshot', 'capture', 'zoom')): return 'capture'
    return 'advanced'


def summary(name, definition):
    if name in SUMMARIES: return SUMMARIES[name]
    # The full definition remains available, without the CLI's cut-off one-liners.
    paragraph = definition.get('description', '').strip().split('\n\n')[0]
    sentences = re.split(r'(?<=[.!?])\s+(?=[A-Z])', paragraph)
    return sentences[0] if sentences else name


def compact_schema(value):
    """Keep the actual types/constraints; full field prose is opt-in."""
    if isinstance(value, list): return [compact_schema(v) for v in value]
    if isinstance(value, dict):
        return {k: compact_schema(v) for k, v in value.items() if k not in ('description', 'title', '$comment')}
    return value


def describe(name, definition, full=False):
    schema = deepcopy(definition['input_schema'])
    props = schema.get('properties', {})
    if name == 'launch_app':
        schema.setdefault('properties', {})['debug'] = {
            'type': 'boolean', 'default': False,
            'description': 'Include the original launch_receipt for diagnostics. Resolved window fields are unchanged.'}
    if 'include_screenshot' in props:
        props['include_screenshot']['default'] = False
    if name == 'get_window_state':
        for field, limit in (('max_depth', 5), ('max_elements', 256)):
            if field in props: props[field]['default'] = limit
    result = {'name': name, 'summary': summary(name, definition),
              'required': schema.get('required', []), 'warnings': WARNINGS.get(name, []),
              'example': definition.get('example', EXAMPLES.get(name)),
              'input_schema': schema if full else compact_schema(schema)}
    if full: result['description'] = definition.get('description', SUMMARIES.get(name, ''))
    return result
