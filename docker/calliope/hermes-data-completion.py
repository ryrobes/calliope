"""Bounded Data completions using Hermes' provider adapters, without an agent run.

Calliope owns SQL discovery, authorization, validation and cost receipts. Hermes
owns provider credentials and wire formats. There is deliberately no auxiliary
provider fallback: a Data revision stays on its explicitly selected model.
"""
import asyncio
import inspect
import logging
import time
from urllib.parse import urlsplit

from aiohttp import web

log = logging.getLogger(__name__)


def validate(body):
    allowed = {"provider", "model", "messages", "max_tokens", "timeout_seconds"}
    if not isinstance(body, dict) or set(body) - allowed:
        raise ValueError("Only provider, model, messages, max_tokens and timeout_seconds are accepted.")
    for key in ("provider", "model"):
        value = body.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 256:
            raise ValueError(f"An explicit {key} is required.")
    if body["provider"].strip().lower() in {"auto", "automatic"}:
        raise ValueError("Data requires an explicit provider.")
    limit = body.get("max_tokens", 4000)
    if type(limit) is not int or not 1 <= limit <= 16384:
        raise ValueError("max_tokens must be an integer from 1 through 16384.")
    timeout = body.get("timeout_seconds", 90)
    if type(timeout) not in (int, float) or not 5 <= timeout <= 300:
        raise ValueError("timeout_seconds must be a finite number from 5 through 300.")
    messages = body.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 12:
        raise ValueError("Supply between 1 and 12 text messages.")
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("system", "user", "assistant")
                or not isinstance(message["content"], str)):
            raise ValueError("Messages must contain only a text content and a system, user or assistant role.")
    if sum(len(m["content"]) for m in messages) > 256000:
        raise ValueError("Messages must total at most 256000 characters.")
    return dict(body, provider=body["provider"].strip(), model=body["model"].strip(),
                max_tokens=limit, timeout_seconds=timeout)


def field(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


def plain(obj):
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "__dict__"):
        return {k: plain(v) for k, v in vars(obj).items() if not k.startswith("_")}
    if isinstance(obj, dict):
        return {k: plain(v) for k, v in obj.items()}
    return obj


def completion(body):
    from agent import auxiliary_client as aux
    from hermes_cli.runtime_provider import resolve_runtime_provider

    provider, model = body["provider"], body["model"]
    started = time.monotonic()
    # Resolve credentials in Hermes, using the same provider catalog as chat.
    # The custom endpoint must be concrete before entering the auxiliary router,
    # whose unconfigured-custom branch otherwise attempts provider discovery.
    runtime = resolve_runtime_provider(requested=provider, target_model=model)
    runtime = dict(runtime, model=model)
    if provider == "custom" and not runtime.get("base_url"):
        raise RuntimeError("Selected custom provider has no endpoint")
    client = None
    try:
        with aux.scoped_runtime_main(runtime):
            client, resolved_model = aux.resolve_provider_client(
                provider, model=model, main_runtime=runtime,
                explicit_base_url=runtime.get("base_url"),
                explicit_api_key=runtime.get("api_key"), api_mode=runtime.get("api_mode"))
            if client is None or not resolved_model:
                raise RuntimeError("Selected provider has no completion client")
            base_url = str(getattr(client, "base_url", "") or runtime.get("base_url") or "")
            kwargs = aux._build_call_kwargs(
                provider, resolved_model, body["messages"], max_tokens=body["max_tokens"],
                timeout=body["timeout_seconds"], base_url=base_url)
            # Use the shared transport's stream-only adaptations, but not its
            # auto-discovery/recovery ladder or background-task configuration.
            response = aux._create_with_progress(
                client, kwargs, force_stream=aux._provider_requires_stream(provider, base_url))
        usage = plain(field(response, "usage")) or {}
        choices = field(response, "choices", []) or []
        choice = choices[0] if choices else None
        message = field(choice, "message")
        content = field(message, "content", "")
        if isinstance(content, list):
            content = "".join(field(p, "text", "") for p in content
                              if field(p, "type") in {"text", "output_text"})
        # Never substitute private reasoning or invoke returned tool requests.
        content = content.strip() if isinstance(content, str) else ""
        receipt = {
            "usage": usage, "model_version": field(response, "model") or resolved_model,
            "provider_request_id": field(response, "id"),
            "endpoint_host": urlsplit(base_url).hostname,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
        result = {"provider": provider, "model": model, "receipt": receipt}
        if not content or field(message, "tool_calls"):
            return dict(result, error={"code": "data_completion_empty",
                "message": "The selected model returned no usable Data answer. Please try again."})
        return dict(result, content=content, finish_reason=field(choice, "finish_reason"))
    finally:
        # resolve_provider_client returns a fresh client (not the auxiliary cache).
        target = client if callable(getattr(client, "close", None)) else getattr(client, "_client", None)
        close = getattr(target, "close", None)
        if callable(close) and not inspect.iscoroutinefunction(close):
            try:
                close()
            except Exception:
                pass


async def handle(adapter, request):
    auth_error = adapter._check_auth(request)
    if auth_error is not None:
        return auth_error
    body, error = await adapter._read_json_body(request)
    if error is not None:
        return error
    try:
        body = validate(body)
    except ValueError as exc:
        return web.json_response({"error": {"code": "data_completion_invalid", "message": str(exc)}}, status=400)
    try:
        result = await asyncio.wait_for(asyncio.to_thread(completion, body), body["timeout_seconds"] + 5)
        return web.json_response(result, status=502 if result.get("error") else 200)
    except Exception as exc:
        # Do not send raw SDK errors, credentials, URLs or request content back.
        log.warning("Data completion failed (%s)", type(exc).__name__)
        timed_out = isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower()
        return web.json_response({"error": {
            "code": "data_completion_timeout" if timed_out else "data_completion_failed",
            "message": ("The selected Calliope model timed out while building the Data query. Please try again."
                        if timed_out else "Calliope could not complete the Data query with the selected model. Check its provider connection and try again."),
        }}, status=504 if timed_out else 502)
