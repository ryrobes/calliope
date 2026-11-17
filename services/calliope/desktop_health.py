"""Read-only Computer checks for System; never start a run or acquire control."""
import asyncio
import os
from urllib.parse import urlsplit

import httpx


async def inspect(config):
    component = {'id': 'desktop', 'label': 'Desktop / computer'}
    if not config.desktop_debug_enabled:
        return {**component, 'state': 'not_enabled',
                'detail': 'Computer use is switched off. A desktop may still be installed; no connection was attempted.'}
    required = {'desktop_url': 'control address', 'desktop_control_key': 'control key',
                'desktop_vnc_basic': 'control viewer credential',
                'desktop_vnc_view_basic': 'read-only viewer credential'}
    missing = [label for field, label in required.items() if not getattr(config, field, '')]
    if missing:
        return {**component, 'state': 'needs_setup', 'detail': 'Missing ' + ', '.join(missing) + '.'}
    base = config.desktop_url.rstrip('/')
    host = urlsplit(base).hostname or 'desktop'
    viewer = os.getenv('CALLIOPE_DESKTOP_VNC_URL', f'https://{host}:6901').rstrip('/')

    async def control():
        try:
            async with httpx.AsyncClient(timeout=4, trust_env=False) as client:
                response = await client.get(base + '/health', headers={
                    'X-Calliope-Desktop-Key': config.desktop_control_key})
            if response.status_code in (401, 403):
                return 'Control API rejected its credential.'
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get('ok') is not True:
                return 'Control API did not report readiness.'
            if data.get('vnc_ready') is not True or data.get('display_ready') is not True:
                return 'Control API responded, but the desktop display is not ready.'
        except (httpx.HTTPError, ValueError):
            return 'Control API did not return a valid health response; check the desktop deployment and connection.'

    async def screen():
        try:
            # Same private Kasm TLS endpoint and read-only account as ComputerGateway.
            # This loads only the viewer shell; it does not open a VNC session.
            async with httpx.AsyncClient(timeout=4, verify=False, trust_env=False) as client:
                response = await client.get(viewer + '/vnc.html', headers={
                    'Authorization': config.desktop_vnc_view_basic})
            if response.status_code in (401, 403):
                return 'Desktop viewer rejected its read-only credential.'
            response.raise_for_status()
            if 'text/html' not in response.headers.get('content-type', '') or not response.content:
                return 'Desktop viewer did not return its HTML interface.'
        except httpx.HTTPError:
            return 'Desktop viewer could not be reached; check its address and network access.'

    errors = [error for error in await asyncio.gather(control(), screen()) if error]
    return {**component, 'state': 'unavailable' if errors else 'reachable',
            'detail': ' '.join(errors) if errors else
                'Control API, display and authenticated viewer responded. Model inference is checked separately under Capabilities.'}
