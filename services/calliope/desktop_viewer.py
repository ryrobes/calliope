"""Compatibility adapter for the protocol-matched client in our pinned Kasm image."""
import re

from desktop_access import DesktopError


REVISION = b"calliope-lifetime-1"
UI_ASSET = re.compile(r"assets/ui-[\w-]+\.js\Z")
UI_IMPORT = re.compile(rb'(src="\./assets/ui-[\w-]+\.js)(")')
IDLE_GUARD = b',s>l?(wt("Idle Disconnect reached, disconnecting rfb session...")'
OWNED_GUARD = b',/* calliope: viewer lifetime is gateway-owned */!1?(wt("Idle Disconnect reached, disconnecting rfb session...")'


def adapt_ui(content):
    # Kasm treats any iframe as KasmVDI and disconnects after 20 minutes without
    # HUMAN input. Watching an agent does not update lastActiveAt. Zero is not a
    # supported off switch in this version; it disconnects immediately instead.
    # Disable only that branch, retaining the existing RFB heartbeat and all
    # gateway authentication, generation revocation and ownership checks.
    if content.count(IDLE_GUARD) != 1:
        raise DesktopError("VIEWER_VERSION_UNSUPPORTED",
                           "The desktop viewer needs a compatibility update.", 503)
    return content.replace(IDLE_GUARD, OWNED_GUARD, 1)


def adapt_html(content):
    # The upstream hash describes the unmodified bundle. Give our adapter its
    # own cache key so a recently cached, unpatched client cannot survive deploy.
    content, count = UI_IMPORT.subn(rb'\1?adapter=' + REVISION + rb'\2', content)
    if count != 1:
        raise DesktopError("VIEWER_VERSION_UNSUPPORTED",
                           "The desktop viewer needs a compatibility update.", 503)
    return content


# Keep the recovery page local and authenticated even when the guest is down.
# The parent owns the original iframe URL (including read-only preview mode).
DISCONNECTED_HTML = b'''<!doctype html><html class="noVNC_disconnected" lang="en">
<head><meta charset="utf-8"><title>Desktop disconnected</title>
<link rel="stylesheet" href="/calliope/computer-viewer.css">
<script defer src="/calliope/computer-viewer.js"></script></head>
<body data-calliope-disconnected="true"><div id="noVNC_container"></div>
<noscript>Desktop disconnected. Hide and reopen the desktop to reconnect.</noscript>
</body></html>'''
