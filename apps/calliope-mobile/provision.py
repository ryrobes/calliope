#!/usr/bin/env python3
"""Install the probe and provision the user's existing session into private app storage.

No credentials in the APK, command line, repository, shared phone storage or logs.
ADB reverse exposes the existing desktop tunnel on the phone's own loopback.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

PACKAGE = 'ai.rvbbit.calliope.mobile'

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--serial', required=True, help='Explicit adb device serial')
    p.add_argument('--connection', type=Path, default=Path.home()/'.config/calliope-canvas/connection.json')
    p.add_argument('--apk', type=Path, default=Path(__file__).parent/'dist/calliope-mobile.apk')
    p.add_argument('--adb', default=shutil.which('adb') or str(Path.home()/'.local/share/calliope-mobile/platform-tools/adb'))
    args = p.parse_args()
    adb = [args.adb, '-s', args.serial]
    config = json.loads(args.connection.read_text())
    base = urlparse(config['base_url'])
    loopback = base.hostname in ('localhost', '127.0.0.1', '::1')
    if base.username or base.password or not (base.scheme == 'https' or base.scheme == 'http' and loopback):
        raise SystemExit('Connection needs HTTPS or a local HTTP tunnel')
    req = Request(config['base_url'].rstrip('/')+'/api/calliope/ui-apps', headers={'Cookie': 'wh_session='+config['session_cookie']})
    with build_opener(NoRedirect).open(req, timeout=15) as response:
        if 'application/json' not in response.headers.get('Content-Type', ''):
            raise SystemExit('Calliope did not return JSON; reconnect the desktop client first')
        apps = json.load(response).get('apps', [])
    subprocess.run(adb+['get-state'], check=True, capture_output=True)
    subprocess.run(adb+['install', '-r', str(args.apk)], check=True)
    if loopback:
        port = base.port or (443 if base.scheme == 'https' else 80)
        subprocess.run(adb+['reverse', f'tcp:{port}', f'tcp:{port}'], check=True)
        # Preserve hostname for HTTPS certificate validation; normalize HTTP loopback.
        if base.scheme == 'http':
            config['base_url'] = urlunparse(base._replace(netloc=f'127.0.0.1:{port}'))
    browser = urlparse(config['browser_url'])
    if browser.hostname in ('localhost', '127.0.0.1', '::1'):
        port = browser.port or (443 if browser.scheme == 'https' else 80)
        subprocess.run(adb+['reverse', f'tcp:{port}', f'tcp:{port}'], check=True)
    payload = json.dumps({k: config.get(k, '') for k in ('base_url', 'browser_url', 'session_cookie', 'email')}).encode()
    subprocess.run(adb+['shell', 'run-as', PACKAGE, 'sh', '-c', "'umask 077; mkdir -p files; cat > files/connection.json'"], input=payload, check=True, capture_output=True)
    probe = Path(__file__).parent/'target/aarch64-linux-android/release/ui-probe'
    if probe.is_file():
        subprocess.run(adb+['shell', 'run-as', PACKAGE, 'sh', '-c', "'cat > files/ui-probe; chmod 700 files/ui-probe'"], input=probe.read_bytes(), check=True, capture_output=True)
    subprocess.run(adb+['shell', 'am', 'force-stop', PACKAGE], check=True)
    subprocess.run(adb+['shell', 'am', 'start', '-n', PACKAGE+'/.CalliopeActivity'], check=True)
    print(f'Connected native experiment; {len(apps)} published apps available.')

if __name__ == '__main__':
    main()
