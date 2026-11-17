"""Loopback browser origin preserves routing and authentication semantics."""
import importlib.util
import socket
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import pytest

spec = importlib.util.spec_from_file_location('browser_origin', Path(__file__).resolve().parents[1] / 'docker/calliope/browser-origin.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_only_local_http_origin_gets_proxy():
    assert m.addresses('https://example.com', 'http://origin:80') is None
    assert m.addresses('http://example.com', 'http://origin:80') is None
    assert m.addresses('http://localhost:18080', 'http://origin:80') == (('127.0.0.1',18080),('origin',80))
    with pytest.raises(ValueError): m.addresses('http://secret@localhost:18080', 'http://origin:80')


def test_proxy_preserves_host_cookies_and_auth_denials():
    received=[]
    class Page(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_GET(self):
            received.append((self.headers['Host'],self.headers.get('Cookie')))
            ok=self.headers.get('Cookie')=='session=fixture'
            self.send_response(200 if ok else 401);self.end_headers();self.wfile.write(b'private app' if ok else b'login required')
    origin=ThreadingHTTPServer(('127.0.0.1',0),Page)
    proxy=m.OriginProxy(('127.0.0.1',0),origin.server_address)
    for server in (origin,proxy):threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        url=f'http://127.0.0.1:{proxy.server_address[1]}/d/test'
        with pytest.raises(HTTPError) as exc: urlopen(Request(url,headers={'Host':'localhost:18080'}),timeout=5)
        assert exc.value.code==401
        with urlopen(Request(url,headers={'Host':'localhost:18080','Cookie':'session=fixture'}),timeout=5) as response:
            assert response.read()==b'private app'
        assert received==[('localhost:18080',None),('localhost:18080','session=fixture')]
    finally:
        for server in (proxy,origin):server.shutdown();server.server_close()
