"""Instance inference connection, shared with Clover through a private mounted secret.

Only Calliope can write the directory; inference services mount it read-only.
No key is returned, logged, saved in observations, or forwarded to chat.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

import httpx


def key_path():
    value = os.getenv('CALLIOPE_OPENROUTER_KEY_FILE', '').strip()
    return Path(value) if value else None


def status():
    path = key_path()
    configured = False
    updated_at = None
    if path:
        try:
            configured = bool(path.read_text().strip())
            updated_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except (OSError, UnicodeError):
            pass
    return {'provider': 'openrouter', 'configured': configured, 'editable': path is not None,
            'updated_at': updated_at, 'scope': 'Instance AI: Hermes and Clover share OpenRouter for chat, SQL AI, embeddings, decisions, media and research. Local workers use their own models.'}


def sync_hermes(conn_factory=None):
    """Synchronize from the shared mount; the secret never crosses the control API."""
    import provider_settings
    try:
        result = provider_settings._control('POST', '/v1/providers/instance-key', {}, timeout=300)
        if not result.get('ok') or not result.get('shared'):
            raise RuntimeError('Shared instance key management is not enabled in Hermes')
    except Exception:
        return {'ok': False, 'message': 'The instance key is saved, but Hermes synchronization is pending. Check the saved key to retry; Clover already uses it.'}
    catalog_refreshed = False
    if conn_factory is not None:
        try:
            provider_settings.refresh(conn_factory, models=True)
            catalog_refreshed = True
        except Exception:
            pass  # Key installation succeeded; discovery can be retried independently.
    return {'ok': True, 'restarted': bool(result.get('restarted')), 'catalog_refreshed': catalog_refreshed,
            'message': ('OpenRouter is connected for both Hermes and Clover. '
                        + ('Hermes restarted with the shared key.' if result.get('restarted') else 'Hermes already has the shared key.')
                        + (' Model discovery is pending; use Refresh models to retry.' if conn_factory and not catalog_refreshed else ''))}


def validate_key(token, *, transport=None):
    token = str(token or '').strip()
    if not token or len(token.encode()) > 65536 or any(c.isspace() for c in token):
        raise ValueError('Enter an OpenRouter API key without spaces.')
    try:
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False, transport=transport) as client:
            response = client.get('https://openrouter.ai/api/v1/key', headers={'Authorization': 'Bearer ' + token})
        if response.status_code in (401, 403):
            raise ValueError('OpenRouter did not accept this key. The current key has not changed.')
        response.raise_for_status()
        data = response.json().get('data')
        if not isinstance(data, dict) or data.get('disabled'):
            raise ValueError('OpenRouter did not confirm an active key. The current key has not changed.')
    except (httpx.HTTPError, TypeError, AttributeError):
        raise ValueError('OpenRouter could not be reached to check the key. The current key has not changed.') from None
    return token


def save_key(value, *, transport=None, conn_factory=None):
    path = key_path()
    if not path:
        raise ValueError('Instance key management is not enabled on this deployment.')
    token = validate_key(value, transport=transport)
    # The parent is a deliberately mounted secret directory, not an arbitrary client path.
    # 0644 permits the different unprivileged worker UIDs to read this one shared
    # secret. Its host parent is private and workers have read-only mounts.
    fd, temporary = tempfile.mkstemp(prefix='.openrouter-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(token + '\n')
            stream.flush()
            os.fchmod(stream.fileno(), 0o644)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    hermes = sync_hermes(conn_factory)
    return {**status(), 'checked_at': datetime.now(timezone.utc).isoformat(),
            'hermes': hermes, 'message': hermes['message']}


def check_key(*, transport=None, conn_factory=None):
    path = key_path()
    try:
        value = path.read_text() if path else ''
    except (OSError, UnicodeError):
        value = ''
    validate_key(value, transport=transport)
    hermes = sync_hermes(conn_factory)
    return {**status(), 'checked_at': datetime.now(timezone.utc).isoformat(),
            'hermes': hermes, 'message': hermes['message']}
