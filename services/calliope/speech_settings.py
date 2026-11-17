"""Authenticated internal Hermes speech control; never returns provider keys."""
import base64
import os

import httpx


async def control(method="GET", path="", body=None, *, timeout=75):
    url = os.environ.get("WAREHOUSE_HERMES_CONTROL_URL", "http://hermes:8643").rstrip("/")
    key = os.environ.get("HERMES_CONTROL_KEY") or os.environ.get("WAREHOUSE_HERMES_API_KEY", "")
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10, timeout)), follow_redirects=False) as client:
        response = await client.request(method, url + "/v1/speech" + path,
                                        headers={"Authorization": "Bearer " + key}, json=body)
    if response.status_code >= 400:
        message = "Hermes speech service is unavailable."
        if response.status_code == 400:
            message = str(response.json().get("error") or message)
        raise ValueError(message)
    return response.json()


async def capabilities(config):
    try:
        result = await control(timeout=4)
    except (httpx.HTTPError, ValueError):
        result = {"enabled": False, "realtime": {"enabled": False},
                  "error": "Hermes speech service is unavailable."}
    return {**result, "max_audio_bytes": config.max_audio_bytes,
            "max_audio_seconds": config.max_audio_seconds, "retains_audio": False}


async def transcribe(payload, extension):
    return await control("POST", "/transcriptions", {
        "audio": base64.b64encode(payload).decode("ascii"), "extension": extension})
