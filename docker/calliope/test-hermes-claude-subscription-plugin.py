"""Build-time gate: the Claude subscription DirectSDK plugin is registered as a bundled
model provider, its catalog resolves the short aliases, and the pinned Claude Code CLI
is installed and answers `auth status` (logged out) under an isolated config dir.
Runs inside the image with cwd /opt/hermes and the Hermes venv python."""
import importlib.util
import os
import pathlib
import shutil
import subprocess
import sys

sys.path.insert(0, "/opt/hermes")
PROVIDER = "claude-subscription-directsdk-experimental"
PLUGIN_DIR = pathlib.Path("/opt/hermes/plugins/model-providers") / PROVIDER
CLI_VERSION = "2.1.299"

assert (PLUGIN_DIR / "plugin.yaml").is_file(), f"plugin not baked at {PLUGIN_DIR}"
manifest = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
assert "kind: model-provider" in manifest and f"name: {PROVIDER}" in manifest, manifest[:200]

import providers  # noqa: E402  (hermes registry; discovery imports every bundled provider plugin)

profile = providers.get_provider_profile(PROVIDER)
assert profile is not None, "plugin profile not registered from plugins/model-providers"
assert profile.name == PROVIDER, profile.name
assert PROVIDER in {p.name for p in providers.list_providers()}, "provider missing from list_providers()"

def _load(name: str):
    # The plugin dual-imports (package-relative first, flat second); loaded standalone it
    # takes the flat path, which needs the plugin dir on sys.path — as its own tests do.
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    return importlib.import_module(name)

catalog = _load("model_catalog")
aliases = getattr(catalog, "ALIASES", {})
for short in ("sonnet", "haiku", "opus", "fable"):
    assert short in aliases, f"alias {short!r} missing from the plugin catalog: {sorted(aliases)}"

claude = shutil.which("claude")
assert claude, "claude CLI missing from PATH (npm install -g @anthropic-ai/claude-code@<pinned>)"
version = subprocess.run([claude, "--version"], capture_output=True, text=True, timeout=60).stdout.strip()
assert version.split(maxsplit=1)[0] == CLI_VERSION, f"claude CLI is {version!r}, expected {CLI_VERSION}"

setup = _load("directsdk_setup")
gate_env = dict(os.environ, CLAUDE_CONFIG_DIR="/tmp/claude-gate-config")
status = setup.setup_status(env=gate_env)
assert status.get("available") is True, status
assert status.get("logged_in") is False, status
shutil.rmtree("/tmp/claude-gate-config", ignore_errors=True)
print(f"claude-subscription plugin gate: OK (claude {version}, provider {PROVIDER} registered, aliases {sorted(aliases)})")
