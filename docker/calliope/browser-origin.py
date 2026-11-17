"""Mirror an HTTP loopback public origin for the container-local browser.

Only the configured public port is bound, only on IPv4 loopback. The fixed
internal origin receives unmodified HTTP/WebSocket bytes, including Host and
cookies. This supplies reachability, never an identity or authorization bypass.
"""
import os
import select
import socket
import socketserver
import threading
from urllib.parse import urlsplit


def addresses(public_url, internal_origin):
    public, internal = urlsplit(public_url), urlsplit(internal_origin)
    if public.scheme != 'http' or public.hostname not in ('localhost', '127.0.0.1'):
        return None
    if public.username or public.password or public.path not in ('', '/') or public.query or public.fragment:
        raise ValueError('Browser public origin must be an origin, without credentials')
    if internal.scheme != 'http' or not internal.hostname or internal.username or internal.password:
        raise ValueError('Browser internal origin must be HTTP without credentials')
    return ('127.0.0.1', public.port or 80), (internal.hostname, internal.port or 80)


class OriginProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, listen, upstream):
        self.upstream = upstream
        super().__init__(listen, Relay)


class Relay(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            with socket.create_connection(self.server.upstream, timeout=10) as upstream:
                upstream.settimeout(30)
                self.request.settimeout(30)
                peers = {self.request: upstream, upstream: self.request}
                while True:
                    readable, _, _ = select.select(list(peers), [], [], 300)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        peers[source].sendall(data)
        except OSError:
            # Closed sockets or unavailable origin are transport failures. Never
            # manufacture a successful HTML response that could fool a probe.
            return


def start_from_env():
    try:
        pair = addresses(os.environ.get('CALLIOPE_BROWSER_PUBLIC_URL', ''),
                         os.environ.get('CALLIOPE_BROWSER_INTERNAL_ORIGIN', 'http://origin:80'))
        if pair is None:
            return None
        proxy = OriginProxy(*pair)
        threading.Thread(target=proxy.serve_forever, name='browser-origin', daemon=True).start()
        print(f'[browser-origin] loopback public origin available on port {pair[0][1]}', flush=True)
        return proxy
    except (ValueError, OSError) as exc:
        print(f'[browser-origin] unavailable ({type(exc).__name__}); use capture_live_app for private apps', flush=True)
        return None
