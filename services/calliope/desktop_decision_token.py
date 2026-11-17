"""Short-lived, job-scoped credentials for the optional computer chooser."""
import base64
import hashlib
import hmac
import json
import time


def issue(key, run_id, job_id, generation, timeout):
    payload = base64.urlsafe_b64encode(json.dumps({
        "purpose": "computer-decision", "run_id": run_id, "job_id": job_id,
        "generation": generation, "expires": time.time() + timeout,
    }, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def verify(key, token):
    if not key or len(token) > 2000:
        raise ValueError("Invalid computer decision credential")
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid computer decision credential")
    value = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if value["purpose"] != "computer-decision" or value["expires"] < time.time():
        raise ValueError("Expired computer decision credential")
    return value
