"""Read-only release and deployment inventory. No Docker socket or updater in the app."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path


def image_name(ref):
    return str(ref or '').split('@', 1)[0].rsplit('/', 1)[-1].split(':', 1)[0]


def inventory():
    manifest = Path(__file__).with_name('calliope-release.json')
    if not manifest.is_file():
        manifest = Path(__file__).parents[2] / 'release/calliope-release.json'
    release = json.loads(manifest.read_text())
    path = Path(os.getenv('CALLIOPE_DEPLOYMENT_INVENTORY_PATH', '/run/calliope/deployment/inventory.json'))
    observed, warning = {}, None
    try:
        observed = json.loads(path.read_text())
        if not isinstance(observed.get('containers'), list):
            raise ValueError('Invalid inventory')
    except FileNotFoundError:
        warning = 'Installed image inventory has not been supplied by this deployment.'
    except (ValueError, OSError):
        observed = {}
        warning = 'The deployment image inventory could not be read.'
    # Only publish explicit inventory fields, never arbitrary environment/config.
    rows = [{k: c.get(k) for k in ('container', 'image', 'image_id', 'digests', 'version', 'revision', 'state', 'health', 'host')}
            for c in observed.get('containers', []) if isinstance(c, dict) and c.get('image')]
    version = release['calliope_version']
    components = []
    for name in release['images']:
        installed = [r for r in rows if image_name(r['image']) == name or r['container'] == name]
        components.append({'name': name, 'target': name + ':' + version, 'kind': 'release',
                           'installed': installed, 'reused_from': release.get('reused_images', {}).get(name, {}).get('from_version')})
    for key, ref in release.get('upstreams', {}).items():
        if key.endswith('_base_image'):
            continue  # Build inputs are not separately deployed services.
        components.append({'name': key.removesuffix('_image'), 'target': ref, 'kind': 'upstream',
                           'installed': [r for r in rows if image_name(r['image']) == image_name(ref)]})
    known = {r['container'] for c in components for r in c['installed']}
    for row in rows:
        if row['container'] not in known:
            components.append({'name': row['container'], 'target': None, 'kind': 'additional', 'installed': [row]})
    captured = observed.get('captured_at')
    stale = True
    try:
        stale = (datetime.now(timezone.utc) - datetime.fromisoformat(captured.replace('Z', '+00:00'))).total_seconds() > 86400
    except (ValueError, TypeError, AttributeError):
        pass
    return {'release_version': version, 'engine_version': release.get('rvbbit_engine_version'),
            'captured_at': captured, 'stale': stale, 'warning': warning, 'components': components,
            'upgrades_enabled': False, 'registry_checked': False}


def capability_power(capability):
    """Configuration evidence, not a claim about a remote provider's hardware."""
    route, option, package = (capability.get(k) or {} for k in ('route', 'option', 'package'))
    source, runtime = route.get('source'), option.get('runtime', '')
    model = str(capability.get('model_version') or capability.get('model') or '')
    if capability.get('id') == 'web_research' and source == 'hosted-provider':
        return {'label': 'CPU + API', 'detail': 'CPU research service with hosted inference and search.'}
    if runtime in ('cpu-default', 'optional-cpu') and '/openrouter/' in model:
        return {'label': 'CPU + API', 'detail': 'CPU worker with a hosted embedding dependency in its configured model contract.'}
    if source == 'hosted-provider' or model.startswith(('openrouter/', 'clover-decisions-v1/', 'clover-text-judgments-v1/', 'fish-s2.1-pro-')):
        return {'label': 'API', 'detail': 'Hosted model API; provider hardware is not part of this installation.'}
    if source == 'legacy-clover':
        return {'label': 'API', 'detail': 'Remote Clover service; its hardware is managed separately.'}
    if package.get('gpu_required') is True or runtime == 'optional-gpu':
        return {'label': 'GPU', 'detail': 'Configured GPU worker or GPU worker deployment option.'}
    if package.get('gpu_required') is False or runtime in ('cpu-default', 'optional-cpu'):
        return {'label': 'CPU', 'detail': 'Configured CPU service or CPU worker deployment option.'}
    return {'label': 'Worker', 'detail': 'Worker hardware has not been declared.'}
