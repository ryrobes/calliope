"""Data's selected-model client; all provider auth and transports stay in Hermes."""
import json
import os
import urllib.error
import urllib.request


class CompletionError(ValueError):
    def __init__(self, message, receipt=None):
        super().__init__(message)
        self.receipt = receipt or {}


class DataCompletionClient:
    def __init__(self, provider, model):
        self.selected_provider, self.model = provider, model
        self.backend_name = "clover_llm" if provider in {"custom", "clover"} else provider
        base = os.environ.get("WAREHOUSE_HERMES_URL", "").strip().rstrip("/")
        self._key = os.environ.get("WAREHOUSE_HERMES_API_KEY", "").strip()
        if not base or not self._key:
            raise ValueError("Calliope’s model connection is not configured for Data.")
        self.endpoint_url = base + "/v1/data/completion"

    def complete_json(self, *, system, user, max_tokens=4000):
        body = {"provider": self.selected_provider, "model": self.model,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                "max_tokens": max_tokens, "timeout_seconds": 90}
        request = urllib.request.Request(self.endpoint_url, data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"}, method="POST")
        status = 200
        try:
            try:
                response = urllib.request.urlopen(request, timeout=100)
            except urllib.error.HTTPError as exc:
                response, status = exc, exc.code
            with response:
                payload = json.loads(response.read(2 * 1024 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("Invalid completion envelope")
        except Exception:
            raise CompletionError("Calliope’s selected model is unavailable for Data. Please try again.") from None
        receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
        if status != 200 or payload.get("error"):
            code = (payload.get("error") or {}).get("code") if isinstance(payload.get("error"), dict) else None
            message = {
                "data_completion_timeout": "The selected Calliope model timed out while building the Data query. Please try again.",
                "data_completion_empty": "The selected model returned no usable Data answer. Please try again.",
            }.get(code, "Calliope could not complete the Data query with the selected model. Check its provider connection and try again.")
            raise CompletionError(message, receipt)
        if payload.get("provider") != self.selected_provider or payload.get("model") != self.model:
            raise CompletionError("Calliope returned an unexpected model route for Data.", receipt)
        content = payload.get("content")
        if not isinstance(content, str) or not content.strip():
            raise CompletionError("The selected model returned no usable Data answer. Please try again.", receipt)
        return content, receipt
