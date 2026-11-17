#!/usr/bin/env python3
"""Exercise shipping HTTPS routing with disposable upstreams, not a product boot."""
import argparse
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]


def run(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(' '.join(args[:4]) + ': ' + result.stderr[-6000:])
    return result


UPSTREAM = '''
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json,threading
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.server.server_port == 8765 and self.path == '/api/calliope/account/settings':
            cookie=self.headers.get('Cookie','')
            status={'wh_session=test-admin':200,'wh_session=test-user':403,'wh_session=auth-down':503}.get(cookie,401)
            self.send_response(status);self.send_header('Content-Length','0');self.end_headers();return
        raw=self.rfile.read(int(self.headers.get('Content-Length','0')))
        data=json.dumps({'port':self.server.server_port,'path':self.path,'method':self.command,
                         'host':self.headers.get('Host'),'proto':self.headers.get('X-Forwarded-Proto'),
                         'body':raw.decode()}).encode()
        self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
    do_POST=do_GET
    def log_message(self,*args): pass
threading.Thread(target=ThreadingHTTPServer(('0.0.0.0',3000),Handler).serve_forever,daemon=True).start()
ThreadingHTTPServer(('0.0.0.0',8765),Handler).serve_forever()
'''


def behind_proxy(args, network, caddyfile, trusted):
    """Plain listener as configured by CALLIOPE_TLS=proxy; returns the scheme the app sees."""
    proxy = network + '-plain-' + uuid.uuid4().hex[:6]
    try:
        run('docker', 'run', '-d', '--name', proxy, '--network', network,
            '-v', str(caddyfile) + ':/etc/caddy/Caddyfile:ro', '-e', 'CALLIOPE_TRUSTED_PROXIES=' + trusted,
            '-e', 'WAREHOUSE_UPSTREAM=upstream:8765', '-e', 'LENS_UPSTREAM=upstream:3000',
            '-e', 'WAREHOUSE_PUBLIC_URL=https://calliope.test', args.caddy_image)
        address = json.loads(run('docker', 'inspect', proxy).stdout)[0]['NetworkSettings']['Networks'][network]['IPAddress']
        for attempt in range(80):
            try:
                connection = http.client.HTTPConnection(address, 80, timeout=10)
                connection.request('GET', '/health', headers={'Host': 'calliope.test', 'X-Forwarded-Proto': 'https'})
                response = connection.getresponse()
                body = json.loads(response.read())
                assert (response.status, body['host']) == (200, 'calliope.test'), body
                return body['proto']
            except (OSError, http.client.HTTPException, ValueError):
                if attempt == 79:
                    raise
                time.sleep(.25)
    finally:
        run('docker', 'rm', '-f', '-v', proxy, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--caddy-image', default='caddy:2-alpine')
    parser.add_argument('--python-image', default='python:3.12-alpine')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    name = 'calliope-origin-test-' + uuid.uuid4().hex[:12]
    upstream, proxy = name + '-upstream', name + '-proxy'
    run('docker', 'network', 'create', '--internal', name)
    try:
        with tempfile.TemporaryDirectory(prefix=name) as temp:
            temp = Path(temp)
            config = (ROOT / 'docker/origin/Caddyfile.calliope').read_text()
            # Test the real routes with an isolated CA; no DNS/ACME traffic.
            config = config.replace('{$ORIGIN_LISTEN::80} {', '{$ORIGIN_LISTEN::80} {\n\ttls internal')
            config = config.replace('{$BURROW_LISTEN::81} {', '{$BURROW_LISTEN::81} {\n\ttls internal')
            (temp / 'Caddyfile').write_text(config)
            run('docker', 'run', '-d', '--name', upstream, '--network', name,
                '--network-alias', 'upstream', '--entrypoint', 'python3', args.python_image,
                '-u', '-c', UPSTREAM)
            run('docker', 'run', '-d', '--name', proxy, '--network', name,
                '-v', str(temp / 'Caddyfile') + ':/etc/caddy/Caddyfile:ro',
                '-e', 'ORIGIN_LISTEN=calliope.test', '-e', 'WAREHOUSE_UPSTREAM=upstream:8765',
                '-e', 'BURROW_LISTEN=calliope.test:3001',
                '-e', 'DATARABBIT_PUBLIC_URL=https://calliope.test:3001',
                '-e', 'WAREHOUSE_PUBLIC_URL=https://calliope.test',
                '-e', 'LENS_UPSTREAM=upstream:3000', args.caddy_image)
            # On Linux the host can reach this internal test bridge directly;
            # no host port is published and the containers have no Internet route.
            info = json.loads(run('docker', 'inspect', proxy).stdout)[0]
            address = info['NetworkSettings']['Networks'][name]['IPAddress']
            for _ in range(80):
                ca = run('docker', 'exec', proxy, 'cat', '/data/caddy/pki/authorities/local/root.crt', check=False)
                if ca.returncode == 0:
                    break
                time.sleep(.25)
            else:
                raise RuntimeError('Test HTTPS CA was not created')
            context = ssl.create_default_context(cadata=ca.stdout)

            def request(method, path, body=None, *, port=443, headers=None, status=200):
                connection = http.client.HTTPSConnection('calliope.test', port, context=context, timeout=10)
                connection.sock = context.wrap_socket(socket.create_connection((address, port), timeout=10),
                                                      server_hostname='calliope.test')
                try:
                    connection.request(method, path, body=body, headers=headers or {})
                    response = connection.getresponse()
                    payload = response.read()
                    assert response.status == status, (path, response.status, payload)
                    return json.loads(payload) if status == 200 else dict(response.getheaders())
                finally:
                    connection.close()

            for attempt in range(80):
                try:
                    request('GET', '/health')
                    break
                except (OSError, http.client.HTTPException, AssertionError):
                    if attempt == 79:
                        raise
                    time.sleep(.25)
            paths = ['/calliope', '/mcp', '/mcp/transport', '/.well-known/oauth-protected-resource',
                     '/.well-known/oauth-authorization-server', '/authorize', '/token', '/register',
                     '/auth/callback', '/health']
            for path in paths:
                response = request('GET', path)
                assert (response['port'], response['path'], response['proto'], response['host']) == (
                    8765, path, 'https', 'calliope.test'), response
            assert request('GET', '/login')['port'] == 3000
            assert request('POST', '/login', 'login-probe')['port'] == 8765
            assert request('POST', '/mcp', '{"jsonrpc":"2.0"}')['body'] == '{"jsonrpc":"2.0"}'
            assert request('GET', '/datarabbit', status=302)['Location'] == 'https://calliope.test:3001'
            assert request('GET', '/', port=3001, status=302)['Location'] == 'https://calliope.test/login?next=/datarabbit'
            assert request('GET', '/login', port=3001, status=302)['Location'] == 'https://calliope.test/login?next=/datarabbit'
            request('GET', '/api/db/connections', port=3001, status=401)
            request('GET', '/api/db/connections', port=3001, headers={'Cookie':'wh_session=test-user'}, status=403)
            request('GET', '/', port=3001, headers={'Cookie':'wh_session=auth-down'}, status=503)
            admin = {'Cookie':'wh_session=test-admin', 'Origin':'https://calliope.test:3001'}
            for path in ['/', '/hub', '/_next/static/app.js', '/api/db/connections']:
                response = request('GET', path, port=3001, headers=admin)
                assert (response['port'], response['path'], response['proto'], response['host']) == (3000, path, 'https', 'calliope.test:3001'), response
            response = request('POST', '/api/db/query', '{"sql":"SELECT 1"}', port=3001, headers=admin)
            assert response['method'] == 'POST' and response['body'] == '{"sql":"SELECT 1"}'
            request('POST', '/api/db/connections', '{}', port=3001, headers={**admin,'Origin':'https://evil.test'}, status=403)
            request('POST', '/api/db/connections', '{}', port=3001, headers={'Cookie':'wh_session=test-admin'}, status=403)
            # Internal networks report no gateway; the host reaches them from inside the subnet.
            subnet = json.loads(run('docker', 'network', 'inspect', name).stdout)[0]['IPAM']['Config'][0]['Subnet']
            behind = {trusted: behind_proxy(args, name, (ROOT / 'docker/origin/Caddyfile.calliope'), trusted)
                      for trusted in (subnet, '192.0.2.1/32')}
            # An operator's HTTPS proxy keeps its forwarded scheme; any other client cannot claim it.
            assert behind == {subnet: 'https', '192.0.2.1/32': 'http'}, behind
            result = {'verified_https': True, 'remote_mcp_routes': paths,
                      'forwarded_public_origin': True, 'login_routing': True, 'mcp_post_body': True,
                      'datarabbit_https': True, 'datarabbit_admin_gate': True,
                      'datarabbit_fail_closed': True, 'datarabbit_csrf': True, 'datarabbit_post_body': True,
                      'operator_proxy_trust': True}
            args.report.write_text(json.dumps(result, indent=2) + '\n')
            print(json.dumps(result))
    except BaseException:
        logs = run('docker', 'logs', proxy, check=False)
        print((logs.stdout + logs.stderr)[-6000:])
        raise
    finally:
        for container in (proxy, upstream):
            run('docker', 'rm', '-f', '-v', container, check=False)
        run('docker', 'network', 'rm', name)


if __name__ == '__main__':
    main()
