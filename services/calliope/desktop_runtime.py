"""HTTP adapter to a computer guest, whether local or on a separate VM."""
from pathlib import Path
from urllib.parse import quote
import hashlib
import os
import uuid

import httpx

from desktop_access import DesktopError


class DesktopRuntime:
    def __init__(self, config):
        if not config.desktop_enabled:
            raise DesktopError("DESKTOP_DISABLED", "The computer is not enabled on this installation.", 404)
        self.base = config.desktop_url.rstrip("/")
        self.headers = {"X-Calliope-Desktop-Key": config.desktop_control_key}

    def request(self, method, path, payload=None):
        with httpx.Client(timeout=45, trust_env=False) as client:
            response = client.request(method, self.base + "/computer/" + path,
                                      headers=self.headers, json=payload)
        try:
            result = response.json()
        except ValueError:
            raise DesktopError("DESKTOP_UNAVAILABLE", "The computer returned an invalid response.", 502) from None
        if response.is_error:
            error = result.get("error") or {}
            raise DesktopError(error.get("code", "DESKTOP_FAILED"),
                               error.get("message", "The computer rejected the request."), response.status_code)
        return result

    def download(self, key, destination: Path, sha256=None):
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp = destination.with_name(destination.name + "." + uuid.uuid4().hex + ".part")
        digest = hashlib.sha256()
        try:
            with httpx.Client(timeout=120, trust_env=False) as client:
                with client.stream("GET", self.base + "/computer/files/" + quote(key, safe="/"), headers=self.headers) as response:
                    response.raise_for_status()
                    with temp.open("wb") as f:
                        for chunk in response.iter_bytes():
                            digest.update(chunk)
                            f.write(chunk)
                        f.flush()
                        os.fsync(f.fileno())
            if sha256 and digest.hexdigest() != sha256:
                raise DesktopError("ARTIFACT_MISMATCH", "The computer artifact failed its checksum.", 502)
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
        return destination
