"""One observed choice/action/verification inside an ordinary Python job.

No planning loop, auto-retry or confidence threshold. The calling program owns
its task and assertions; an unresolved action returns control via an exception.
"""
import json
from pathlib import Path


class ActionNeedsAttention(RuntimeError):
    def __init__(self, result):
        self.result = result
        super().__init__(json.dumps(result, ensure_ascii=False))


def signature(element):
    return json.dumps({k: element.get(k) for k in
                      ('role', 'label', 'frame', 'actions', 'enabled', 'selected', 'value')}, sort_keys=True)


def candidates(state):
    return [e for e in state.get('elements', []) if
            e.get('element_token') and e.get('label') and e.get('enabled') is not False and
            set(e.get('actions') or []) & {'click', 'press', 'activate', 'toggle'}]


def act(client, chooser, *, goal, pid, window_id, query=None, provider='jev', visual=False, expect=None):
    if not isinstance(goal, str) or not goal.strip(): raise ValueError('Supply an immediate action goal')
    if provider not in ('jev', 'cua-s1'): raise ValueError('Choose jev or cua-s1')
    if visual and provider != 'cua-s1': raise ValueError('Visual choices require provider="cua-s1"')
    if expect is not None and (not isinstance(expect, list) or not expect):
        raise ValueError('expect must contain verify_state predicates')
    anchor = {'pid': pid, 'window_id': window_id}
    observe = {**anchor, **({'query': query} if query else {})}
    evidence = {'goal': goal, 'provider': provider, 'dispatch_attempted': False, 'verified': None}
    def stop(reason, **extra):
        raise ActionNeedsAttention({**evidence, 'status': 'needs_attention', 'reason': reason, **extra})
    try:
        # Have the driver validate the complete predicate contract before input.
        # This sample is not evidence of success; verification runs again after input.
        if expect is not None:
            client.call('verify_state', {**anchor, 'expect': expect, 'include_screenshot': False,
                                         'timeout_ms': 0, 'stable_samples': 1})
        before = client.call('get_window_state', {**observe, 'include_screenshot': visual})
    except Exception as exc:
        stop('Observation or expectation validation failed; no click sent.', error=str(exc))
    evidence['before'] = before
    targets = candidates(before)
    # Reserve an explicit abstention option; never silently drop viable targets.
    limit = 15 if provider == 'jev' else 25
    if not targets: stop('No actionable exposed controls. Use browser() or inspect a fresh screenshot.')
    if len(targets) > limit: stop(f'{len(targets)} candidates exceed the {limit}-control choice size. Narrow query or act directly.')
    if any(len(str(e['label'])) > 1000 for e in targets): stop('A target label is too large for the chooser; use a narrower/direct interaction.')
    options = [{'id': f'target_{i}', 'role': e.get('role') or 'control', 'label': e['label'],
                'action': 'click', 'entity_id': e['element_token']} for i, e in enumerate(targets)]
    options.append({'id': 'reobserve', 'role': 'fallback', 'label': 'None of these controls reliably accomplishes the goal; inspect again', 'action': 'reobserve'})
    # Only action-relevant structured context is sent, not duplicate full tree prose.
    observation = json.dumps({'app': before.get('app_name'), 'title': before.get('window_title'),
                              'coverage': before.get('coverage'),
                              'controls': [{k: e.get(k) for k in ('role', 'label', 'frame', 'value', 'selected')} for e in targets]}, ensure_ascii=False)
    if len(observation) > 32000: stop('Observation exceeds chooser size; narrow the query or act directly.')
    screenshot = None
    if visual:
        path = before.get('screenshot_file_path')
        if not path: stop('The visual chooser requires a fresh captured image.')
        try:
            screenshot = Path(path).read_bytes()
        except OSError as exc:
            stop('Fresh image could not be read; no click sent.', error=str(exc))
    try:
        decision = chooser(goal=goal, observation=observation, options=options, provider=provider,
                           app=before.get('app_name') or 'Desktop', screenshot=screenshot)
    except Exception as exc:
        stop('Chooser unavailable; reobserve or continue directly.', error=str(exc))
    evidence['decision'] = decision
    if not isinstance(decision, dict): stop('Chooser did not return a structured decision.')
    selected = decision.get('selected_id')
    if selected == 'reobserve': stop('Chooser requested a fresh observation.')
    ids = [o['id'] for o in options[:-1]]
    if selected not in ids: stop('Chooser did not return a supplied target.')
    target = targets[ids.index(selected)]
    evidence['selected'] = {'label': target['label'], 'role': target.get('role')}
    # A newer snapshot supplies a new token. Match exact observed identity and
    # layout, not an old index; let Cua also reject any subsequent token staleness.
    try:
        fresh = client.call('get_window_state', {**observe, 'include_screenshot': False})
    except Exception as exc:
        stop('Could not refresh target; no click sent.', error=str(exc))
    current = candidates(fresh)
    if (any(before.get(k) != fresh.get(k) for k in ('window_bounds', 'window_title', 'app_name')) or
            sorted(map(signature, targets)) != sorted(map(signature, current))):
        stop('Controls changed while the chooser was running; no click sent.', after=fresh)
    matched = [e for e in current if signature(e) == signature(target)]
    if len(matched) != 1: stop('Target identity is ambiguous; no click sent.', after=fresh)
    try:
        # Set before dispatch: a lost response must never invite a blind retry.
        evidence['dispatch_attempted'] = True
        evidence['action'] = client.call('click', {**anchor, 'element_token': matched[0]['element_token']})
    except Exception as exc:
        stop('Action response failed or is uncertain. Observe before any retry.', error=str(exc))
    try:
        if expect is not None:
            verification = client.call('verify_state', {**anchor, 'expect': expect, 'include_screenshot': False})
            evidence['verification'] = verification
            # The driver's predicate result, not transport success, determines this.
            evidence['verified'] = verification.get('status') == 'satisfied'
    except Exception as exc:
        stop('Action was sent but its outcome could not be established.', error=str(exc))
    try:
        evidence['after'] = client.call('get_window_state', {**observe, 'include_screenshot': False})
    except Exception as exc:
        if evidence['verified']:
            evidence['after_observation_error'] = str(exc)
        else:
            stop('Action was sent but its outcome could not be established.', error=str(exc))
    if expect is not None and not evidence['verified']:
        stop('Action was sent but the expected outcome was not verified.')
    return {**evidence, 'status': 'verified' if evidence['verified'] else 'acted_unverified'}
