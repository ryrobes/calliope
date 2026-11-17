"""Renew first-party browser identity for the active Computer run.

Cookies travel only over the authenticated guest control channel. They are not
tool results, run context, recordings, or learning evidence. The normal browser
authorization paths still decide which artifacts and actions this actor owns.
"""
import logging
import os
import threading
import time
from urllib.parse import urlsplit

import desktop_access as access
from desktop_runtime import DesktopRuntime

COOKIE = '__Host-calliope_computer'
TTL = 300
_conn_factory = None
_worker = None
_lock = threading.Lock()
log = logging.getLogger(__name__)


def origin():
    value = os.environ.get('WAREHOUSE_PUBLIC_URL', '').rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Computer browser authentication requires an HTTPS public origin')
    return value


def read_session(request):
    """An invalid Computer cookie never falls back to a saved human login."""
    import auth
    if _conn_factory is None:
        return None
    try:
        public = origin()
        # A cookie is host-bound by Chrome; check the request host too so a
        # copied credential cannot authenticate to another configured origin.
        if request.url.netloc != urlsplit(public).netloc:
            return None
        claims = auth.jwt.decode(request.cookies[COOKIE], auth.JWT_SECRET,
                                 algorithms=[auth.JWT_ALG], audience=public,
                                 issuer=public, options={'require': ['exp', 'iat', 'rid', 'sub', 'idt', 'mpd']})
        if claims.get('typ') != 'computer-session':
            return None
        rid = access.uid(claims['rid'])
        with _conn_factory() as conn:
            row = conn.execute(
                "SELECT requester FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid AND status='active'",
                (rid,)).fetchone()
            if not row or row['requester'] != claims.get('idt'):
                return None
        # This session acts for the requester; the device is not their profile.
        # Keep its provenance in via/run_id, never in human name/avatar claims.
        return {'sub': claims['sub'], 'identity': claims['idt'], 'mapped': claims['mpd'],
                'via': 'computer', 'name': '', 'picture': '', 'computer_run_id': rid}
    except Exception:
        # Auth outages deny this identity rather than using a different cookie.
        return None


def sync(conn_factory, config, runtime=None, state=None):
    import auth
    runtime = runtime or DesktopRuntime(config)
    state = state or runtime.request('GET', 'status')
    access.sync_runs(conn_factory, state)
    rid = state.get('active_run')
    if not rid:
        return {'status': 'idle'}
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT requester FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid AND status='active'",
            (access.uid(rid),)).fetchone()
    if not row:
        return {'status': 'pending'}
    actor = row['requester']
    if not auth.JWT_SECRET:
        raise ValueError('Computer browser authentication requires the server signing key')
    # Resolve execution roles on the server, never from guest input. Shared
    # appliances use actor directly; Burrow retains its existing role mapping.
    subject, mapped = auth.session_subject(actor, 'computer')
    public = origin()
    now = int(time.time()) // 60 * 60
    token = auth.jwt.encode({'typ': 'computer-session', 'sub': subject, 'idt': actor,
                             'mpd': mapped, 'rid': rid, 'iat': now, 'exp': now + TTL,
                             'iss': public, 'aud': public}, auth.JWT_SECRET, algorithm=auth.JWT_ALG)
    result = runtime.request('POST', 'browser-auth', {
        'run_id': rid, 'origin': public, 'cookie': token, 'expires_at': now + TTL})
    if result.get('status') == 'ready':
        access.append_event(conn_factory, rid, actor, 'browser.authenticated',
                            {'origin': public, 'identity': actor, 'run_id': rid,
                             'renewal': 'automatic', 'credential_omitted': True}, 'browser.authenticated')
    return result


def prepare(conn_factory, config, runtime=None, state=None):
    """Browser startup need not prevent native desktop work; retry in background."""
    if not os.environ.get('WAREHOUSE_PUBLIC_URL'):
        return {'status': 'unconfigured'}
    try:
        return sync(conn_factory, config, runtime, state)
    except Exception as exc:
        log.warning('Computer browser identity pending: %s', type(exc).__name__)
        return {'status': 'pending', 'message': 'First-party browser sign-in is retrying automatically.'}


def start_worker(conn_factory, config):
    global _conn_factory, _worker
    _conn_factory = conn_factory
    with _lock:
        if _worker and _worker.is_alive():
            return
        def work():
            while True:
                prepare(conn_factory, config)
                threading.Event().wait(10)
        _worker = threading.Thread(target=work, name='computer-browser-auth', daemon=True)
        _worker.start()
