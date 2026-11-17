"""Small, observed native subtask loop. No generated code or learned replay.

The caller supplies the intent and optional outcome predicates. Jev selects
only supplied actions, a hierarchy limits each decision, and every input uses
fresh observations. Unknown outcomes return to the caller with evidence.
"""
from collections import defaultdict
import hashlib
import json
import time

from computer_dialogs import observe
from computer_health import health, progress_history


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def identity(control):
    return {k: control.get(k) for k in ('label', 'role', 'bounds', 'frame', 'actions', 'interaction', 'enabled', 'selected', 'checked', 'focused', 'editable', 'value', 'value_complete', 'region', 'source')}


def screen_map(client, *, pid=None, window_id=None, visual_fallback=True):
    state = observe(client, pid=pid, window_id=window_id, surface=True, visual_fallback=visual_fallback)
    target = state.get('target', {})
    dialog = state.get('blocking_dialog')
    actions, groups = [], defaultdict(list)
    for control in state['controls']:
        group = 'active_dialog' if dialog else control.get('region', 'window')
        entry = {**identity(control), 'clickability': control.get('clickability', 'native')}
        # OCR still contributes evidence, but text recognition alone cannot
        # turn a label into an executable button.
        if (control.get('click_arguments') and control.get('label') and control.get('enabled') is not False
                and control.get('source') != 'ocr' and (control.get('actions') or control.get('interaction') in ('select', 'focus'))
                and control.get('role') not in ('file chooser', 'dialog', 'frame', 'panel', 'filler', 'table')):
            action = {'id': f'a{len(actions)}', 'label': control['label'], 'role': control.get('role', 'control'),
                      'kind': 'click', 'region': group, 'identity': identity(control),
                      'tool': 'click', 'arguments': control['click_arguments']}
            actions.append(action)
            entry['action_id'] = action['id']
        groups[group].append(entry)
    # Shortcuts are scoped to the foreground Calc document, never its dialog.
    # Save is useful even when the AX tree is incomplete. No edits/values are inferred.
    if (state.get('status') == 'observed' and state.get('foreground') and not dialog
            and 'libreoffice calc' in target.get('title', '').casefold()):
        for label, key, modifiers in [('Save workbook', 's', ['ctrl']),
                                       ('Save workbook as', 's', ['ctrl', 'shift']),
                                       ('Open file', 'o', ['ctrl'])]:
            actions.append({'id': f'a{len(actions)}', 'label': label, 'role': 'application command',
                'kind': 'shortcut', 'region': 'Calc commands', 'identity': {'label': label},
                # Window-scoped foreground delivery restores the previous
                # window after input, which can steal focus from a new dialog.
                'tool': 'press_key', 'arguments': {'scope': 'desktop',
                    'key': key, 'modifiers': modifiers, 'delivery_mode': 'foreground'}})
    fingerprint = hashlib.sha256(encoded({
        'target': {k: target.get(k) for k in ('window_id', 'owner_pid', 'title', 'bounds')},
        'foreground': state.get('foreground'), 'dialog': dialog,
        'controls': sorted([identity(c) for c in state['controls']], key=encoded),
    }).encode()).hexdigest()
    return {'status': state['status'], 'fingerprint': fingerprint, 'foreground': state.get('foreground', False),
            'target': {k: target.get(k) for k in ('window_id', 'owner_pid', 'title', 'bounds')},
            'blocking_dialog': dialog, 'groups': dict(groups), 'actions': actions,
            'document_regions': state.get('regions', []), 'coverage': state.get('coverage'),
            'observation': state.get('observation', {'status': 'available'}),
            'warnings': state['warnings'],
            'visual': {k: v for k, v in state.get('visual', {}).items() if k != 'controls'},
            'note': 'Only the active dialog is actionable while it blocks the document. OCR text is evidence, not a button. '
                    'Use work for a bounded native subtask; type paths/values directly when needed. No match does not prove absence.'}


def decision_context(scene, history):
    return encoded({'window': scene['target'], 'blocking_dialog': scene['blocking_dialog'],
                    'current_values': [a['identity'] for a in scene['actions'] if a['role'] in ('combo box', 'check box', 'toggle button', 'page tab') or a['identity'].get('editable')],
                    'recent_actions': history[-4:], 'coverage': scene['coverage'],
                    'instruction': 'Choose a useful next action for the supplied goal. Read the complete dialog question. '
                                   'Return to Callie if the task needs typing, more evidence, or is already done. '
                                   'Never click a confirmation merely because it is the only button.'})


def compact_map(scene):
    if scene is None: return None
    return {**scene,
            'groups': {name: {'action_ids': [c['action_id'] for c in controls if 'action_id' in c],
                             'other_controls': [{k: c.get(k) for k in ('label', 'role', 'enabled', 'source', 'bounds', 'value', 'focused', 'editable', 'selected')}
                                                for c in controls if 'action_id' not in c]}
                       for name, controls in scene['groups'].items()},
            'actions': [{**{k: v for k, v in a.items() if k != 'identity'},
                         'state': {k: a['identity'].get(k) for k in ('selected', 'checked', 'focused', 'editable', 'value', 'value_complete')}}
                        for a in scene['actions']]}


def choice_label(action):
    if action['identity'].get('interaction') == 'focus':
        return ('Return for typing in ' if action['identity'].get('editable') and action['identity'].get('focused') else 'Focus ') + action['label'] + ' (current value: ' + str(action['identity'].get('value', 'unavailable'))[:256] + ')'
    if action['role'] == 'page tab':
        return 'Select tab ' + action['label'] + (' (currently selected)' if action['identity'].get('selected') else '')
    if action['role'] == 'combo box':
        return 'Change selection (currently ' + action['label'] + ')'
    if action['role'] in ('check box', 'toggle button'):
        return 'Toggle ' + action['label'] + (' (currently on)' if action['identity'].get('checked') else ' (currently off)')
    return action['label']


def select(chooser, goal, scene, history, provider):
    actions = scene['actions']
    exact = [a for a in actions if a['label'].strip().casefold() == goal.strip().casefold()]
    if len(exact) == 1:
        return exact[0], [{'route': 'exact_label', 'selected_id': exact[0]['id']}]
    trace = []
    # Each level has at most 12 actions/groups plus a return option. Grouping
    # is discovery only; it cannot execute anything or hide a discarded tail.
    for _ in range(4):
        grouped = len(actions) > 12
        if grouped:
            regions = defaultdict(list)
            for action in actions: regions[action['region']].append(action)
            buckets = [v[i:i+12] for v in regions.values() for i in range(0, len(v), 12)]
            if len(buckets) > 12:
                size = (len(actions) + 11) // 12
                buckets = [actions[i:i+size] for i in range(0, len(actions), size)]
            options = [{'id': f'g{i}', 'role': 'action group', 'action': 'inspect',
                        'label': (bucket[0]['region'] + ': ' + ', '.join(choice_label(a) for a in bucket))[:1000]}
                       for i, bucket in enumerate(buckets)]
        else:
            options = [{'id': a['id'], 'role': a['role'], 'label': choice_label(a)[:1000], 'action': a['kind']} for a in actions]
        if not options: return None, trace
        options.append({'id': 'return_to_callie', 'role': 'fallback', 'action': 'return',
                        'label': 'Done, unclear, or needs typing/another capability; return observations to Callie'})
        instruction = (goal + '\nChoose the next useful input. Leave fields whose current values already match the goal unchanged. '
                       'Prefer advancing the task over opening a matching value selector. Return if no useful input is available.')
        response = chooser(goal=instruction, observation=decision_context(scene, history), options=options,
                           provider=provider, app=scene['target'].get('title') or 'Desktop')
        trace.append(response)
        if not isinstance(response, dict): raise ValueError('Chooser returned no structured decision')
        selected = response.get('selected_id')
        if selected == 'return_to_callie': return None, trace
        if selected not in {o['id'] for o in options}: raise ValueError('Chooser returned an unknown action')
        if grouped:
            actions = buckets[int(selected[1:])]
        else:
            return next(a for a in actions if a['id'] == selected), trace
    return None, trace


def work(client, chooser, *, goal, pid, window_id, expect=None, max_actions=4, provider='jev'):
    if not isinstance(goal, str) or not goal.strip() or len(goal) > 3500:
        raise ValueError('Supply a concrete immediate goal of at most 3500 characters')
    if type(max_actions) is not int or not 1 <= max_actions <= 6:
        raise ValueError('max_actions must be between 1 and 6')
    if provider not in ('jev', 'cua-s1'): raise ValueError('Choose jev or cua-s1')
    if expect is not None and (not isinstance(expect, list) or not expect):
        raise ValueError('expect must be a nonempty list of verify_state predicates')
    start = time.monotonic()
    steps, decisions, seen = [], [], set()
    scene = None
    def finish(status, reason, verified=False, code=None, diagnostics=None):
        history = progress_history(pid, window_id, code) if code or status in ('progress', 'verified') else None
        return {'status': status, 'reason': reason, 'verified': verified, 'goal': goal,
                'code': code, 'health': diagnostics, 'progress_history': history,
                'elapsed_ms': round((time.monotonic() - start) * 1000), 'steps': steps,
                'decisions': decisions, 'screen': compact_map(scene),
                'next': 'Continue from this fresh screen; do not blindly repeat the same action. '
                        'progress means a UI transition, not verified task completion.'}
    def blocked(code, reason):
        diagnostic = health(pid=pid, window_id=window_id)
        if diagnostic.get('responsive') is False:
            code = 'application_unresponsive'
            reason = 'The application event loop did not respond. Repeated clicks or switching input backends will not repair it.'
        result = finish('needs_attention', reason, code=code, diagnostics=diagnostic)
        result['next'] = ('Report this concrete blocker. Preserve unsaved work; use an already-authorized application restart if appropriate. '
                          'Do not debug X11, modify overlays, or repeat equivalent inputs during the user task. '
                          'This is an application/observation failure, not a request for Take over permission.')
        if diagnostic.get('responsive') is True and code == 'observation_unavailable':
            result['next'] = ('The application responds but accessibility is unavailable. Use a fresh screenshot and one known UI input, '
                              'then verify its effect. Do not repeatedly request the same failed tree. '
                              'If that also has no effect, report this blocker instead of trying more input backends.')
        failures = result['progress_history']['consecutive_failures']
        result['recovery'] = {'recommended': diagnostic.get('responsive') is False or failures >= 2,
                              'automatic_restart': False, 'consecutive_failures': failures}
        if failures >= 2:
            result['next'] = (f'{failures} consecutive failed work attempts for this application in this run. '
                              'Report the blocker or use an already-authorized application recovery. '
                              'Do not repeat equivalent inputs, switch backends, or debug X11 during the task. '
                              'Preserve unsaved work unless discard is already authorized.')
        return result
    def read():
        # One bounded read retry handles a transient bridge failure. No input
        # and no chooser are repeated. OCR is reserved for the caller fallback.
        value = screen_map(client, pid=pid, window_id=window_id, visual_fallback=False)
        if value.get('observation', {}).get('status') == 'unavailable':
            time.sleep(.15)
            value = screen_map(client, pid=pid, window_id=window_id, visual_fallback=False)
        return value
    def verify():
        return client.call('verify_state', {'pid': pid, 'window_id': window_id, 'expect': expect,
                           'include_screenshot': False, 'timeout_ms': 0, 'stable_samples': 1}, timeout=5)
    def input_handoff(field):
        result = finish('progress', 'The selected editable field is focused and ready for caller input; no value was entered.')
        result['handoff'] = 'caller_input'
        result['field'] = {k: field.get(k) for k in ('label', 'role', 'focused', 'value', 'value_complete')}
        result['next'] = 'Type the explicit value directly, then reobserve and continue. Do not reopen the dialog or refocus this field.'
        return result
    try:
        if expect is not None:
            check = verify()  # Validate before input; an already-met goal needs no action.
            if check.get('status') == 'satisfied': return finish('verified', 'Outcome was already observed.', True)
        scene = read()
        for _ in range(max_actions):
            if scene.get('observation', {}).get('status') == 'unavailable':
                return blocked('observation_unavailable', 'Accessibility could not be read after one retry; this does not establish a screen change.')
            if scene['status'] != 'observed' or not scene['foreground']:
                return finish('needs_attention', 'Target is unavailable, changing, or not foreground.')
            action, trace = select(chooser, goal, scene, [s['label'] for s in steps], provider)
            decisions.extend(trace)
            if action is None:
                if steps:
                    result = finish('progress', 'The UI changed and the chooser returned control. Review the new state for caller input or outcome verification.')
                    result['handoff'] = 'caller_input_or_verification'
                    result['next'] = 'Use the returned screen. Type explicit values directly if needed, or verify the outcome. Do not restart the completed navigation.'
                    return result
                return finish('needs_attention', 'No input selected. The goal may already be met or need caller input; inspect the screen before changing approach.', code='no_action_selected')
            signature = (scene['fingerprint'], encoded(action['identity']))
            if signature in seen: return finish('needs_attention', 'Repeated state/action; no additional input sent.')
            fresh = read()
            if fresh.get('observation', {}).get('status') == 'unavailable':
                scene = fresh
                return blocked('observation_unavailable', 'Fresh accessibility read failed; no input sent. This is not evidence the screen changed.')
            if fresh['status'] != 'observed' or fresh['fingerprint'] != scene['fingerprint']:
                scene = fresh
                return finish('needs_attention', 'Screen changed during selection; no input sent for that choice.')
            matches = [a for a in fresh['actions'] if a['kind'] == action['kind'] and a['identity'] == action['identity']]
            if len(matches) != 1: return finish('needs_attention', 'Target is ambiguous; no input sent.')
            action = matches[0]
            field = action['identity']
            editable = field.get('interaction') == 'focus' and field.get('editable')
            if editable and field.get('focused'):
                scene = fresh
                return input_handoff(field)
            seen.add(signature)
            step = {'label': action['label'], 'kind': action['kind'], 'tool': action['tool'],
                    'arguments': action['arguments'], 'before': scene['fingerprint'], 'dispatch_attempted': True}
            steps.append(step)  # A lost response must still show that input may have happened.
            old = scene
            scene = None  # If input/read fails, do not present the old map as fresh.
            step['receipt'] = client.call(action['tool'], action['arguments'], timeout=5)
            # Brief observation-driven settling, never a blind action retry.
            for attempt in range(3):
                if attempt: time.sleep(.15)
                if expect is not None:
                    step['verification'] = verify()
                    if step['verification'].get('status') == 'satisfied':
                        scene = None  # Do not return a pre-action map as current.
                        return finish('verified', 'Explicit outcome predicates satisfied.', True)
                scene = read()
                if scene.get('observation', {}).get('status') == 'unavailable':
                    return blocked('observation_unavailable', 'Input was sent, but its outcome cannot be read. Do not repeat the input.')
                if scene['status'] == 'observed' and scene['foreground'] and scene['fingerprint'] != old['fingerprint']:
                    break
            step['after'] = scene['fingerprint']
            if editable and scene['status'] == 'observed' and scene['foreground']:
                focused = [a['identity'] for a in scene['actions'] if a['identity'].get('focused')
                           and all(a['identity'].get(k) == field.get(k) for k in ('label', 'role', 'bounds'))]
                if len(focused) == 1: return input_handoff(focused[0])
            if scene['fingerprint'] == old['fingerprint']:
                return blocked('no_observed_change', 'No observed change after input; do not repeat it. Verify document/file state or inspect visually.')
            progress_history(pid, window_id)
            if old['blocking_dialog'] and not scene['blocking_dialog'] and expect is None:
                return finish('progress', 'Blocking dialog closed. Check the document/file outcome before reporting success.')
        result = finish('progress', 'Observed UI change; action budget reached. Review the new state before continuing.')
        result['handoff'] = 'action_budget'
        return result
    except Exception as exc:
        detail = getattr(exc, 'result', {})
        if detail.get('status') == 'input_failed':
            if steps: steps[-1]['error'] = detail
            return finish('needs_attention', str(exc), code=detail['code'])
        return finish('needs_attention', f'{type(exc).__name__}: {str(exc)[:600]}', code='operation_failed')
