"""Text-first modal observation, with localized OCR when accessibility is sparse."""
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from computer_observation import capture_path, unobstructed_capture


def worker(mode, pid=None):
    command = [sys.executable, str(Path(__file__).with_name('computer_layers.py')), mode]
    if pid is not None: command.append(str(pid))
    env = dict(os.environ)
    if mode in ('surface', 'ax'):
        # One-shot readers must not prefetch the app's full accessibility
        # cache. Noble needs the isolated libatspi backport for this flag to
        # suppress GetItems; other desktop processes keep the system library.
        env['ATSPI_NO_CACHE'] = '1'
        env['LD_LIBRARY_PATH'] = '/opt/calliope-atspi/lib:' + env.get('LD_LIBRARY_PATH', '')
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=4, check=True, env=env)
        return json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {'error': type(exc).__name__, 'message': 'Observation unavailable within four seconds.'}


def target_layer(layers, window_id=None, pid=None):
    windows = [w for w in layers.get('windows', []) if not w.get('hidden')]
    by_id = {w['window_id']: w for w in windows}
    anchor = by_id.get(window_id)
    if window_id is not None and anchor is None: return None
    if anchor and pid is not None and anchor.get('pid') not in (None, pid): return None
    if anchor is None:
        focused = by_id.get(layers.get('active_window_id'))
        anchor = focused if focused and (pid is None or focused.get('pid') == pid) else next(
            (w for w in reversed(windows) if pid is None or w.get('pid') == pid), None)
    if not anchor: return None
    family = {anchor['window_id']}
    for _ in windows:
        family |= {w['window_id'] for w in windows if w.get('transient_for') in family}
    related = [w for w in windows if w['window_id'] in family]
    dialogs = [w for w in related if w.get('modal') or w.get('dialog') or w.get('transient_for')]
    selected = next((w for w in reversed(dialogs) if w.get('focused')), None) or (dialogs[-1] if dialogs else anchor)
    return {**selected, 'owner_pid': selected.get('pid') or anchor.get('pid') or pid,
            'requested_window_id': window_id, 'related_windows': related}


def click_args(bounds):
    return {'scope': 'desktop', 'delivery_mode': 'foreground',
            'x': bounds['x'] + bounds['width'] / 2, 'y': bounds['y'] + bounds['height'] / 2}


def ocr_rows(tsv, crop):
    rows = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t', quoting=csv.QUOTE_NONE):
        try:
            label, confidence = row['text'].strip(), float(row['conf'])
            if not label or confidence < 0: continue
            left, top, width, height = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
            if min(left, top) < 0 or width <= 0 or height <= 0: continue
            if left + width > crop['width'] or top + height > crop['height']: continue
            bounds = {'x': crop['x'] + left, 'y': crop['y'] + top, 'width': width, 'height': height}
            rows.append({'label': label, 'role': 'text', 'source': 'ocr', 'confidence': confidence / 100,
                         'bounds': bounds, 'click_arguments': click_args(bounds),
                         'clickability': 'unproven'})
        except (KeyError, TypeError, ValueError): continue
    return rows[:150]


def visual_read(client, bounds):
    from computer_capture import Screen
    # Surface reads can reach OCR without making any driver calls first.
    # Load the local catalog so capture can hide our own cursor decoration.
    if hasattr(client, '_catalog'): client._catalog()
    with unobstructed_capture(client, True):
        screen = Screen()
        pixels, width, height, _ = screen.capture()
    x, y = max(0, bounds['x']), max(0, bounds['y'])
    right, bottom = min(width, bounds['x'] + bounds['width']), min(height, bounds['y'] + bounds['height'])
    if right <= x or bottom <= y: raise ValueError('Target is outside the screen')
    crop = dict(x=x, y=y, width=right-x, height=bottom-y)
    path = capture_path()
    screen.pixbuf(pixels, width, height).new_subpixbuf(x, y, right-x, bottom-y).savev(str(path), 'png', [], [])
    result = subprocess.run(['tesseract', str(path), 'stdout', '--psm', '11', 'tsv'],
                            capture_output=True, text=True, timeout=5, check=True)
    return {'source': 'ocr', 'screenshot_file_path': str(path), 'crop_bounds': crop,
            'image_size': {'width': crop['width'], 'height': crop['height']},
            'coordinate_space': 'desktop_pixels', 'controls': ocr_rows(result.stdout, crop),
            'note': 'OCR text boxes are visual estimates, not native controls. Clickability is unproven. '
                    'Coordinates already include the crop origin; do not scale or offset them again.'}


def layer_signature(w):
    return (w.get('window_id'), w.get('pid'), w.get('title'), json.dumps(w.get('bounds'), sort_keys=True))


def transition(previous, current):
    if not previous: return None
    old = {layer_signature(w) for w in previous.get('related_windows', [])}
    new = {layer_signature(w) for w in current.get('related_windows', [])}
    old_dialogs = previous.get('dialogs', [])
    new_dialogs = current.get('dialogs', [])
    appeared = [w for w in current.get('related_windows', []) if layer_signature(w) not in old]
    closed = [w for w in previous.get('related_windows', []) if layer_signature(w) not in new]
    changed = appeared or closed or old_dialogs != new_dialogs
    return {'status': 'changed' if changed else 'no_observed_change', 'appeared': appeared,
            'closed': closed, 'dialogs_changed': old_dialogs != new_dialogs,
            'note': 'A new confirmation dialog is a state transition, not proof the previous click failed. '
                    'Check its question before continuing. This is not verification of task success.'}


def observe(client, *, pid=None, window_id=None, query=None, previous=None, visual_fallback=True, surface=True):
    layers = worker('x11')
    anchor_id = window_id
    if anchor_id is None and previous:
        candidate = previous.get('requested_window_id') or previous.get('target', {}).get('window_id')
        if any(w['window_id'] == candidate for w in layers.get('windows', [])):
            anchor_id = candidate
    selected = target_layer(layers, anchor_id, pid)
    result = {'observed_at': time.time(), 'coordinate_space': 'desktop_pixels',
              'focused_window_id': layers.get('active_window_id'), 'requested_window_id': window_id,
              'controls': [], 'dialogs': [], 'related_windows': [], 'warnings': []}
    if not selected:
        result.update(status='target_unavailable', warnings=[layers.get('message', 'Refresh list_windows; the requested window is unavailable.')])
        result['transition'] = transition(previous, result)
        return result
    result.update(target=selected, related_windows=selected['related_windows'], status='observed')
    foreground = selected['window_id'] == layers.get('active_window_id')
    result['foreground'] = foreground
    if selected.get('modal') or selected.get('dialog'):
        result['blocking_dialog'] = {k: selected.get(k) for k in ('window_id', 'title', 'bounds', 'modal', 'transient_for')}
    if not foreground:
        result['warnings'].append('The target is not foreground. Bring this window forward before using desktop coordinates; native element tokens remain window-targeted.')
    owner = selected.get('owner_pid')
    # Read modal subtrees across the application, including dialogs that lack
    # an X11 PID or are absent from the selected parent window's Cua subtree.
    ax = worker('surface' if surface else 'ax', owner) if owner else {'dialogs': []}
    unavailable = ax.get('error') or (ax.get('coverage') == 'application_not_exposed' and 'application_not_exposed')
    if surface and ax.get('visited') == 0:
        unavailable = unavailable or 'empty_surface'
    result['observation'] = {'status': 'unavailable' if unavailable else 'available',
                             'reason': unavailable or None,
                             'note': 'Accessibility availability is separate from UI change and application liveness.'}
    dialogs = ax.get('dialogs', [])
    # Avoid attaching another document's dialog to an explicitly selected window.
    b = selected['bounds']
    dialogs = [d for d in dialogs if d['bounds']['width'] > 0 and d['bounds']['height'] > 0
               and d['bounds']['x'] < b['x'] + b['width'] and d['bounds']['y'] < b['y'] + b['height']
               and d['bounds']['x'] + d['bounds']['width'] > b['x'] and d['bounds']['y'] + d['bounds']['height'] > b['y']]
    result['dialogs'] = dialogs
    if ax.get('error'): result['warnings'].append('Application accessibility did not respond; using window metadata and visual fallback. Do not repeatedly retry this tree; use a known keyboard command or a focused image.')
    modal = next((d for d in reversed(dialogs) if d.get('focused')), None) or (dialogs[-1] if dialogs else None)
    if modal:
        result['controls'] = [{**c, **({'click_arguments': click_args(c['bounds'])} if foreground else {})} for c in modal['controls']]
        result['blocking_dialog'] = {k: v for k, v in modal.items() if k != 'controls'}
    elif surface:
        # Bounded app walk avoids Cua's potentially expensive virtual Calc tree.
        # Screen-coordinate actions require the selected foreground window.
        def inside(c):
            r = c['bounds']
            return (r['width'] > 0 and r['height'] > 0 and r['x'] >= b['x'] and r['y'] >= b['y']
                    and r['x'] + r['width'] <= b['x'] + b['width']
                    and r['y'] + r['height'] <= b['y'] + b['height'])
        result['controls'] = [{**c, **({'click_arguments': click_args(c['bounds'])} if foreground else {})}
                              for c in ax.get('controls', []) if inside(c)]
        result['regions'] = [r for r in ax.get('regions', []) if inside(r)]
        result['coverage'] = {'source': ax.get('coverage'), 'visited': ax.get('visited'),
                              'complete': False, 'note': 'Bounded controls only; document cells are not enumerated.'}
    # Never silently escalate a bounded observation to a full driver tree.
    # A Calc repro spent its main loop resolving cell screen positions inside
    # the ATK bridge after that fallback; killing the reader did not cancel
    # work already enqueued in the application.
    if not result['controls'] and visual_fallback and foreground:
        try:
            result['visual'] = visual_read(client, modal['bounds'] if modal else selected['bounds'])
            result['controls'] = result['visual']['controls']
        except Exception as exc:
            result['warnings'].append('Localized OCR unavailable: ' + type(exc).__name__ + '. Use look_at_desktop.')
    if query:
        result['controls'] = [c for c in result['controls'] if str(query).casefold() in
                              (str(c.get('label', '')) + ' ' + str(c.get('value', ''))).casefold()]
        if not result['controls']:
            result['warnings'].append('No exposed control matched this query; it does not prove the target is absent. Use a focused image.')
    # Recheck the window identity/layout after a potentially slow bridge/OCR
    # read. Do not offer screen coordinates from a scene that moved underneath.
    latest = worker('x11')
    fresh = target_layer(latest, selected['window_id'], owner)
    if (not fresh or layer_signature(fresh) != layer_signature(selected)
            or latest.get('active_window_id') != layers.get('active_window_id')):
        result['status'] = 'needs_reobserve'
        result['warnings'].append('The foreground window or its bounds changed during observation. Observe again before clicking.')
        result['controls'] = [{k: v for k, v in c.items() if k != 'click_arguments'} for c in result['controls']]
        if 'visual' in result: result['visual']['controls'] = result['controls']
    result['transition'] = transition(previous, result)
    result['next'] = ('Use fresh controls[].click_arguments with cua.call("click", ...), then observe(previous=state). '
                      'OCR can include noninteractive text. If a label/target is ambiguous, inspect look_at_desktop. '
                      'Do not keep querying a sparse parent tree while a dialog blocks it.')
    return result
