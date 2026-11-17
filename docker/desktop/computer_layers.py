"""Bounded, read-only X11 and application-level AT-SPI observations.

Run accessibility in a disposable process: some office bridges stall while
walking a modal. Never enumerate virtual spreadsheet cells or document text.
"""
import ctypes as C
import ctypes.util
from collections import deque
import json
import sys


def compositor_present():
    """Whether X11 has a real compositing-manager owner, not just a setting."""
    x = C.CDLL(ctypes.util.find_library('X11'))
    x.XOpenDisplay.argtypes = [C.c_char_p]; x.XOpenDisplay.restype = C.c_void_p
    x.XDefaultScreen.argtypes = [C.c_void_p]; x.XDefaultScreen.restype = C.c_int
    x.XInternAtom.argtypes = [C.c_void_p, C.c_char_p, C.c_int]; x.XInternAtom.restype = C.c_ulong
    x.XGetSelectionOwner.argtypes = [C.c_void_p, C.c_ulong]; x.XGetSelectionOwner.restype = C.c_ulong
    x.XCloseDisplay.argtypes = [C.c_void_p]
    display = x.XOpenDisplay(None)
    if not display: return False
    try:
        atom = x.XInternAtom(display, f'_NET_WM_CM_S{x.XDefaultScreen(display)}'.encode(), 0)
        return bool(x.XGetSelectionOwner(display, atom))
    finally:
        x.XCloseDisplay(display)


def x11_layers():
    x = C.CDLL(ctypes.util.find_library('X11'))
    ulong, pointer = C.c_ulong, C.c_void_p
    specs = {
        'XOpenDisplay': (pointer, [C.c_char_p]), 'XDefaultRootWindow': (ulong, [pointer]),
        'XInternAtom': (ulong, [pointer, C.c_char_p, C.c_int]),
        'XGetWindowProperty': (C.c_int, [pointer, ulong, ulong, C.c_long, C.c_long, C.c_int, ulong,
            C.POINTER(ulong), C.POINTER(C.c_int), C.POINTER(ulong), C.POINTER(ulong), C.POINTER(pointer)]),
        'XGetGeometry': (C.c_int, [pointer, ulong, C.POINTER(ulong), C.POINTER(C.c_int), C.POINTER(C.c_int),
            C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]),
        'XTranslateCoordinates': (C.c_int, [pointer, ulong, ulong, C.c_int, C.c_int,
            C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(ulong)]),
        'XFree': (C.c_int, [pointer]), 'XCloseDisplay': (C.c_int, [pointer]),
    }
    for name, (restype, argtypes) in specs.items():
        fn = getattr(x, name); fn.restype = restype; fn.argtypes = argtypes
    # Windows can disappear between reads; treat a BadWindow as missing data.
    handler_type = C.CFUNCTYPE(C.c_int, pointer, pointer)
    handler = handler_type(lambda *_: 0)
    x.XSetErrorHandler.argtypes = [handler_type]
    x.XSetErrorHandler(handler)
    display = x.XOpenDisplay(None)
    if not display:
        raise RuntimeError('No X11 display')
    try:
        root = x.XDefaultRootWindow(display)
        def atom(name): return x.XInternAtom(display, name.encode(), 0)
        def prop(win, name):
            actual, fmt, count, left, data = ulong(), C.c_int(), ulong(), ulong(), pointer()
            x.XGetWindowProperty(display, win, atom(name), 0, 4096, 0, 0,
                                 C.byref(actual), C.byref(fmt), C.byref(count), C.byref(left), C.byref(data))
            try:
                if not data: return []
                if fmt.value == 8: return C.string_at(data, count.value).decode('utf-8', 'replace')
                if fmt.value == 32: return list(C.cast(data, C.POINTER(ulong))[:count.value])
                return []
            finally:
                if data: x.XFree(data)
        def first(win, name):
            value = prop(win, name)
            return value[0] if isinstance(value, list) and value else None
        active = first(root, '_NET_ACTIVE_WINDOW')
        ids = prop(root, '_NET_CLIENT_LIST_STACKING') or prop(root, '_NET_CLIENT_LIST') or []
        if active and active not in ids: ids.append(active)
        windows = []
        for wid in ids[-64:]:
            rr, xx, yy, w, h, border, depth = ulong(), C.c_int(), C.c_int(), C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
            if not x.XGetGeometry(display, wid, C.byref(rr), C.byref(xx), C.byref(yy), C.byref(w), C.byref(h), C.byref(border), C.byref(depth)):
                continue
            child = ulong()
            x.XTranslateCoordinates(display, wid, root, 0, 0, C.byref(xx), C.byref(yy), C.byref(child))
            states = prop(wid, '_NET_WM_STATE')
            windows.append({'window_id': wid, 'pid': first(wid, '_NET_WM_PID'),
                'title': prop(wid, '_NET_WM_NAME') or prop(wid, 'WM_NAME') or '',
                'transient_for': first(wid, 'WM_TRANSIENT_FOR'),
                'modal': atom('_NET_WM_STATE_MODAL') in states,
                'dialog': atom('_NET_WM_WINDOW_TYPE_DIALOG') in prop(wid, '_NET_WM_WINDOW_TYPE'),
                'focused': wid == active, 'hidden': atom('_NET_WM_STATE_HIDDEN') in states,
                'bounds': {'x': xx.value, 'y': yy.value, 'width': w.value, 'height': h.value},
                'z_index': len(windows)})
        return {'source': 'x11', 'active_window_id': active, 'windows': windows}
    finally:
        x.XCloseDisplay(display)


def accessible_dialogs(pid, surface=False):
    import pyatspi as ax
    # A live office window can have a dead accessibility bridge. Bound each
    # D-Bus request as well as the overall worker, rather than waiting for the
    # default multi-second timeout before falling back.
    try:
        from gi.repository import Atspi
        Atspi.set_timeout(600, 600)
    except (ImportError, AttributeError):
        pass
    desktop = ax.Registry.getDesktop(0)
    applications = [desktop[i] for i in range(min(desktop.childCount, 128))]
    app = next((a for a in applications if a.get_process_id() == pid), None)
    if app is None: return {'dialogs': [], 'coverage': 'application_not_exposed'}
    if app.childCount < 0:
        return {'dialogs': [], 'controls': [], 'error': 'application_bridge_unavailable',
                'coverage': 'application_bridge_unavailable'}
    dialogs, controls, regions, visited, examined = [], [], [], 0, 0
    def children(node):
        items = [node[i] for i in range(min(node.childCount, 64))]
        # LibreOffice exposes document frames before sibling modal dialogs.
        # Read the dialog first so a dense toolbar cannot consume its budget.
        def priority(item):
            try: return 0 if item.getRoleName() in ('dialog', 'alert') else 1
            except Exception: return 2
        return sorted(items, key=priority)
    roots = children(app)
    visible_dialogs = []
    for child in roots:
        try:
            state = child.getState()
            if state.contains(ax.STATE_SHOWING) and (child.getRoleName() in ('dialog', 'alert') or state.contains(ax.STATE_MODAL)):
                visible_dialogs.append(child)
        except Exception:
            continue
    # A blocked office document's bridge can stall even though its sibling
    # dialog is readable. Do not walk that document after finding the dialog.
    pending = deque((child, 0, None, 'window') for child in (visible_dialogs or roots))
    def walk(node, depth=0, dialog=None, region='window'):
        nonlocal visited, examined
        if visited >= 256 or examined >= 2048 or depth > 12: return
        examined += 1
        try:
            role = node.getRoleName()
            state = node.getState()
            if not state.contains(ax.STATE_SHOWING): return
            name = node.name or ''
            rect = node.queryComponent().getExtents(ax.DESKTOP_COORDS)
            frame = dict(x=rect.x, y=rect.y, width=rect.width, height=rect.height)
            if rect.width <= 0 or rect.height <= 0 or rect.x < -100000 or rect.y < -100000:
                return
            visited += 1
            if role in ('menu bar', 'menu', 'tool bar', 'page tab list'):
                region = (role + ': ' + name).rstrip(': ')
            if surface and role in ('table', 'document spreadsheet', 'document text', 'terminal', 'document web'):
                regions.append({'label': name[:200], 'role': role, 'bounds': frame,
                                'note': 'Content not enumerated; use application data or a focused image.'})
            if role in ('dialog', 'alert') or state.contains(ax.STATE_MODAL):
                dialog = {'label': name, 'role': role, 'modal': state.contains(ax.STATE_MODAL),
                          'bounds': frame, 'controls': [], 'text': [], 'focused': False}
                dialogs.append(dialog)
            if dialog is not None or surface:
                if dialog is not None:
                    if state.contains(ax.STATE_FOCUSED): dialog['focused'] = True
                    if name and role in ('label', 'text', 'paragraph'): dialog['text'].append(name[:1000])
                try:
                    action = node.queryAction()
                    actions = [action.getName(i) for i in range(action.nActions)]
                except (NotImplementedError, AttributeError): actions = []
                def has(flag):
                    value = getattr(ax, flag, None)
                    return value is not None and state.contains(value)
                # Selection and EditableText are separate from Action. GTK
                # tabs, for example, expose Component + SELECTABLE but no
                # Action at all. Keep their native geometry and state.
                editable = has('STATE_EDITABLE')
                selectable = role in ('page tab', 'list item') and has('STATE_SELECTABLE')
                field = role in ('text', 'entry', 'password text', 'combo box', 'spin button') and (editable or has('STATE_FOCUSABLE'))
                if actions or selectable or field:
                    label, label_source = name, 'name'
                    if not label and field:
                        try:
                            for relation in node.getRelationSet():
                                if relation.getRelationType() == ax.RELATION_LABELLED_BY:
                                    label = ' '.join(relation.getTarget(i).name for i in range(min(relation.getNTargets(), 4))).strip()
                                    if label:
                                        label_source = 'labelled_by'; break
                        except Exception: pass  # Optional label metadata must not hide the control.
                        if not label:
                            label = node.description or ''
                            label_source = 'description'
                    control = {'label': label[:500], 'label_source': label_source, 'role': role, 'bounds': frame, 'region': region,
                        'actions': actions, 'enabled': state.contains(ax.STATE_ENABLED),
                        'selected': state.contains(ax.STATE_SELECTED), 'checked': state.contains(ax.STATE_CHECKED),
                        'focused': has('STATE_FOCUSED'), 'editable': editable,
                        'interaction': 'select' if selectable else 'focus' if field else 'activate',
                        'source': 'atspi_dialog' if dialog is not None else 'atspi_surface'}
                    if field and role != 'password text':
                        try:
                            text = node.queryText()
                            control['value'] = text.getText(0, min(text.characterCount, 256))
                            control['value_complete'] = text.characterCount <= 256
                        except Exception: pass  # The field is still usable when its value is unavailable.
                    if dialog is not None: dialog['controls'].append(control)
                    elif surface: controls.append(control)
            # Never recursively inspect office cells or document content.
            if role in ('table', 'tree table', 'document spreadsheet', 'document text', 'terminal', 'document web'):
                return
            for child in children(node):
                pending.append((child, depth + 1, dialog, region))
        except Exception:
            return
    # Breadth-first keeps a file chooser's footer (Save/Cancel) ahead of its
    # potentially enormous directory/format descendants.
    while pending and visited < 256 and examined < 2048:
        walk(*pending.popleft())
    return {'dialogs': dialogs, 'controls': controls, 'regions': regions,
            'coverage': 'bounded_app_surface' if surface else 'bounded_app_dialogs',
            'visited': visited, 'examined': examined, 'coverage_complete': False}


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'ping':
            from computer_health import window_ping
            result = window_ping(int(sys.argv[2]))
        else:
            result = accessible_dialogs(int(sys.argv[2]), surface=sys.argv[1] == 'surface') if sys.argv[1] in ('ax', 'surface') else x11_layers()
        print(json.dumps(result))
    except Exception as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)[:300]}))
