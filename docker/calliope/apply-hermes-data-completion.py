"""Attach Calliope's bounded completion route to the pinned Hermes API server."""
import os
from pathlib import Path

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))
path = root / "gateway/platforms/api_server.py"
source = path.read_text()
route = '            ("POST", "/v1/auxiliary/tts-rewrite", self._handle_auxiliary_tts_rewrite),\n'
method = '    async def _handle_auxiliary_tts_rewrite(self, request: "web.Request") -> "web.Response":\n'
if source.count(route) != 1 or source.count(method) != 1 or "_handle_data_completion" in source:
    raise SystemExit("Pinned Hermes Data completion seam changed")
source = source.replace(route, route + '            ("POST", "/v1/data/completion", self._handle_data_completion),\n')
source = source.replace(method, '''    async def _handle_data_completion(self, request: "web.Request") -> "web.Response":
        from gateway.calliope_data_completion import handle
        with self._profile_scope(_api_request_profile.get()):
            return await handle(self, request)

''' + method)
path.write_text(source)
print("Applied bounded provider-independent Data completions")
