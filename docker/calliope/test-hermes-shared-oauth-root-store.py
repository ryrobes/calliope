"""Build-time check for hermes-shared-oauth-root-store.patch.

A multiplex profile that only *borrows* an OAuth grant from the global root
(no entries of its own) must persist a rotated token pair back to the root
store, not into a profile-local shadow copy — with single-use refresh tokens
a shadow copy diverges on the next rotation and the loser gets "revoked"
(Iris on the shared xAI grant, 2026-08-18). A provider the profile owns keeps
writing locally.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

root = Path(tempfile.mkdtemp(prefix="hermes-root-"))
profile = root / "profiles" / "studio"
profile.mkdir(parents=True)
os.environ["HERMES_HOME"] = str(profile)
os.environ.pop("PYTEST_CURRENT_TEST", None)

root_store = {
    "version": 1,
    "providers": {},
    "credential_pool": {
        "xai-oauth": [{
            "id": "abc123", "label": "Calliope managed key", "auth_type": "oauth",
            "priority": 0, "source": "manual:device_code",
            "access_token": "root-access-1", "refresh_token": "root-refresh-1",
        }],
    },
}
(root / "auth.json").write_text(json.dumps(root_store), encoding="utf-8")
(profile / "auth.json").write_text(json.dumps({"version": 1, "providers": {}, "credential_pool": {}}), encoding="utf-8")

sys.path.insert(0, os.environ.get("HERMES_SRC", "/opt/hermes"))
from hermes_cli import auth as auth_mod  # noqa: E402

assert auth_mod._global_auth_file_path() is not None, "test must run in profile mode"
assert auth_mod._credential_pool_write_target("xai-oauth") == root / "auth.json", "borrowed grant is root-owned"
assert auth_mod._credential_pool_write_target("openrouter") is None, "unknown provider stays local"

# the profile reads the grant through the fallback…
seen = auth_mod.read_credential_pool("xai-oauth")
assert seen and seen[0]["refresh_token"] == "root-refresh-1"

# …and a rotation it performs lands in the ROOT, leaving no shadow copy behind
rotated = dict(seen[0], access_token="root-access-2", refresh_token="root-refresh-2")
auth_mod.write_credential_pool("xai-oauth", [rotated])
root_after = json.loads((root / "auth.json").read_text(encoding="utf-8"))
profile_after = json.loads((profile / "auth.json").read_text(encoding="utf-8"))
assert root_after["credential_pool"]["xai-oauth"][0]["refresh_token"] == "root-refresh-2", root_after
assert not (profile_after.get("credential_pool") or {}).get("xai-oauth"), profile_after
# and every subsequent read (from any profile) sees the rotated pair
assert auth_mod.read_credential_pool("xai-oauth")[0]["refresh_token"] == "root-refresh-2"

# a provider the profile owns still writes locally
auth_mod.write_credential_pool("openrouter", [{
    "id": "k1", "label": "own key", "auth_type": "api_key", "priority": 0,
    "source": "manual", "access_token": "sk-or-own",
}])
profile_after = json.loads((profile / "auth.json").read_text(encoding="utf-8"))
root_after = json.loads((root / "auth.json").read_text(encoding="utf-8"))
assert profile_after["credential_pool"]["openrouter"][0]["id"] == "k1"
assert "openrouter" not in root_after["credential_pool"]
assert auth_mod._credential_pool_write_target("openrouter") is None

# the refresh lock for a borrowed grant is the root's lock, not the profile's
from agent import credential_pool as pool_mod  # noqa: E402

pool = pool_mod.load_pool("xai-oauth")
assert [e.id for e in pool.entries()] == ["abc123"]
assert not (json.loads((profile / "auth.json").read_text(encoding="utf-8")).get("credential_pool") or {}).get("xai-oauth"), \
    "load_pool must not shadow a borrowed grant into the profile store"

print("shared-oauth-root-store: ok")
