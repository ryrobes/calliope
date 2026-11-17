"""Bounded application liveness and per-run progress evidence; never kills apps."""
import ctypes as C
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import time


def window_ping(window_id, timeout=1.2):
    """EWMH ping asks the application's event loop to respond, without UI input."""
    class Data(C.Union):
        _fields_ = [('b', C.c_char * 20), ('s', C.c_short * 10), ('l', C.c_long * 5)]
    class Message(C.Structure):
        _fields_ = [('type', C.c_int), ('serial', C.c_ulong), ('send_event', C.c_int),
                    ('display', C.c_void_p), ('window', C.c_ulong), ('message_type', C.c_ulong),
                    ('format', C.c_int), ('data', Data)]
    class Event(C.Union):
        _fields_ = [('message', Message), ('pad', C.c_long * 24)]
    x = C.CDLL(ctypes.util.find_library('X11'))
    specs = {
        'XOpenDisplay': (C.c_void_p, [C.c_char_p]),
        'XDefaultRootWindow': (C.c_ulong, [C.c_void_p]),
        'XInternAtom': (C.c_ulong, [C.c_void_p, C.c_char_p, C.c_int]),
        'XGetWMProtocols': (C.c_int, [C.c_void_p, C.c_ulong, C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_int)]),
        'XSelectInput': (C.c_int, [C.c_void_p, C.c_ulong, C.c_long]),
        'XSendEvent': (C.c_int, [C.c_void_p, C.c_ulong, C.c_int, C.c_long, C.POINTER(Event)]),
        'XCheckTypedWindowEvent': (C.c_int, [C.c_void_p, C.c_ulong, C.c_int, C.POINTER(Event)]),
        'XFlush': (C.c_int, [C.c_void_p]), 'XFree': (C.c_int, [C.c_void_p]),
        'XCloseDisplay': (C.c_int, [C.c_void_p]),
    }
    for name, (restype, args) in specs.items():
        fn = getattr(x, name); fn.restype = restype; fn.argtypes = args
    # A disappearing window is unavailable, not an exception that exits Python.
    callback = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
    handler = callback(lambda *_: 0)
    x.XSetErrorHandler.argtypes = [callback]; x.XSetErrorHandler(handler)
    display = x.XOpenDisplay(None)
    if not display: return {'status': 'unavailable', 'reason': 'No X11 display'}
    try:
        root = x.XDefaultRootWindow(display)
        ping = x.XInternAtom(display, b'_NET_WM_PING', 0)
        protocol = x.XInternAtom(display, b'WM_PROTOCOLS', 0)
        atoms, count = C.POINTER(C.c_ulong)(), C.c_int()
        supported = x.XGetWMProtocols(display, window_id, C.byref(atoms), C.byref(count))
        try: supported = bool(supported and ping in list(atoms[:count.value]))
        finally:
            if atoms: x.XFree(atoms)
        if not supported: return {'status': 'unsupported', 'responsive': None}
        x.XSelectInput(display, root, 1 << 19)  # SubstructureNotifyMask, on this connection only.
        stamp = int(time.monotonic_ns() // 1000000) & 0xffffffff
        event = Event(); event.message.type = 33; event.message.display = display
        event.message.window = window_id; event.message.message_type = protocol; event.message.format = 32
        event.message.data.l[0:3] = (ping, stamp, window_id)
        if not x.XSendEvent(display, window_id, 0, 0, C.byref(event)):
            return {'status': 'unavailable', 'responsive': None}
        x.XFlush(display); start = time.monotonic()
        while time.monotonic() - start < timeout:
            reply = Event()
            while x.XCheckTypedWindowEvent(display, root, 33, C.byref(reply)):
                m = reply.message
                if m.message_type == protocol and list(m.data.l[:3]) == [ping, stamp, window_id]:
                    return {'status': 'responded', 'responsive': True,
                            'elapsed_ms': round((time.monotonic() - start) * 1000)}
            time.sleep(.02)
        return {'status': 'timed_out', 'responsive': False, 'elapsed_ms': round(timeout * 1000)}
    finally:
        x.XCloseDisplay(display)


def process_state(pid):
    try:
        values = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        return {'pid': int(pid), 'state': values[0], 'start_ticks': values[19],
                'resident_mb': round(int(values[21]) * os.sysconf('SC_PAGE_SIZE') / 1048576, 1)}
    except (OSError, ValueError, IndexError, TypeError):
        return {'pid': pid, 'state': 'unavailable'}


def health(*, pid, window_id):
    from computer_dialogs import worker, target_layer
    layers = worker('x11'); target = target_layer(layers, window_id, pid)
    if not target: return {'status': 'target_unavailable', 'responsive': None, 'process': process_state(pid)}
    # Ping the parent if a modal does not advertise the protocol itself.
    ping = worker('ping', target['window_id'])
    if ping.get('status') == 'unsupported' and target['window_id'] != window_id:
        ping = worker('ping', window_id)
    responsive = ping.get('responsive')
    return {'status': 'responsive' if responsive is True else 'unresponsive' if responsive is False else 'unknown',
            'responsive': responsive, 'ping': ping, 'process': process_state(pid),
            'note': 'Event-loop liveness only; this does not verify document content or prove why an app is stalled.'}


def progress_history(pid, window_id, code=None):
    """Carry failed work observations across jobs in the same run, without blocking raw tools."""
    try:
        return _progress_history(pid, window_id, code)
    except (OSError, ValueError, TypeError):
        # Diagnostics must never hide the original failure or a successful input.
        return {'consecutive_failures': int(code is not None), 'persistence': 'unavailable'}


def _progress_history(pid, window_id, code):
    run = os.environ.get('CALLIOPE_COMPUTER_RUN')
    if not run: return {'consecutive_failures': int(code is not None)}
    root = Path(os.environ.get('CALLIOPE_COMPUTER_PROGRESS_DIR', '/tmp/calliope-computer-progress'))
    root.mkdir(mode=0o700, exist_ok=True)
    key = hashlib.sha256(f'{run}:{pid}:{process_state(pid).get("start_ticks")}'.encode()).hexdigest()
    path = root / (key + '.json')
    try: old = json.loads(path.read_text())
    except (OSError, ValueError): old = {}
    # A long pause may include human recovery; old failures should not linger.
    count = old.get('consecutive_failures', 0) if time.time() - old.get('at', 0) < 300 else 0
    value = {'consecutive_failures': count + 1 if code else 0, 'code': code,
             'window_id': window_id, 'at': time.time()}
    temporary = path.with_suffix('.tmp'); temporary.write_text(json.dumps(value)); temporary.replace(path)
    return value
