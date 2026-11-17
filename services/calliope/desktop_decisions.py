"""Optional Cua-S1/Jev decisions for ordinary desktop Python jobs.

Only a currently running job can call this route. The general Hutch key stays
on the appliance; the guest receives a credential limited to this one route.
"""
import asyncio
import hashlib
import json
import os
import time
import uuid

import httpx
from starlette.responses import JSONResponse

import desktop_access as access
from inference_config import decision_model
from desktop_decision_token import verify


def chooser_base():
    explicit = os.environ.get("CALLIOPE_COMPUTER_CHOOSER_BASE", "").strip()
    if explicit:
        return explicit.rstrip("/")
    shared = os.environ.get("RVBBIT_CLOVER_BASE_URL", "").strip()
    if shared:
        return shared.rstrip("/")
    shared_openai = os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "").strip() or "http://clover:8090/v1"
    return shared_openai.rstrip("/").removesuffix("/v1")


def active(claims, state):
    job = state.get("jobs", {}).get(claims["job_id"], {})
    if (state.get("active_run") != claims["run_id"] or state.get("mode") != "agent"
            or state.get("generation") != claims["generation"]
            or job.get("run_id") != claims["run_id"] or job.get("status") != "running"
            or job.get("generation") != claims["generation"]):
        raise access.DesktopError("CONTROL_CHANGED", "Observe again after control returns to this task.", 409)


def upstream(body):
    provider = body.get("provider", "jev")
    options = body.get("options")
    if provider not in {"cua-s1", "jev"}:
        raise ValueError("Choose cua-s1 or jev")
    if not isinstance(options, list) or not 2 <= len(options) <= (16 if provider == "jev" else 26):
        raise ValueError("Supply distinct candidate options")
    ids = [option.get("id") for option in options if isinstance(option, dict)]
    if len(ids) != len(options) or any(not isinstance(i, str) or not i or len(i) > 80 for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Candidate IDs must be distinct strings")
    candidates = []
    for option in options:
        candidate = {"id": option["id"], "role": option.get("role", "option"),
                     "label": option.get("label"), "action": option.get("action", "select")}
        for field, limit in (("role", 100), ("label", 1000), ("action", 100)):
            value = candidate[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise ValueError("Candidate descriptions must be nonempty strings within the scorer limits")
        if option.get("entity_id") is not None:
            if not isinstance(option["entity_id"], str) or len(option["entity_id"]) > 100:
                raise ValueError("Invalid candidate entity ID")
            candidate["entity_id"] = option["entity_id"]
        candidates.append(candidate)
    goal = body.get("goal")
    observation = body.get("observation")
    if not isinstance(goal, str) or not goal.strip() or not isinstance(observation, str):
        raise ValueError("Supply the goal and a fresh text observation")
    if len(goal) > 4000 or len(observation) > 32000:
        raise ValueError("Decision observation is too large")
    if provider == "jev":
        if body.get("screenshot"):
            raise ValueError("This Jev route uses text observations; use cua-s1 for screenshots")
        item = {"model": decision_model(), "contract": "clover-decisions-v1",
                "state": observation, "questions": {"next_action": {
                    "type": "choice", "instructions": goal,
                    "criteria": {o["id"]: " ".join(o[k] for k in ("role", "label", "action")) for o in candidates}}}}
        return "decisions", {"inputs": [item]}
    app = body.get("app", "Desktop")
    if not isinstance(app, str) or not app.strip() or len(app) > 100:
        raise ValueError("Supply an application name of at most 100 characters")
    if not observation.strip() and not body.get("screenshot"):
        raise ValueError("Text decisions require an observation")
    item = {"goal": goal, "observation": observation, "app": app, "options": candidates}
    if body.get("screenshot"):
        item.update(modality="multimodal", screenshot=body["screenshot"])
    return "computer_decide", {"inputs": [item]}


class Decisions:
    def __init__(self, gateway):
        self.gateway = gateway

    async def route(self, request):
        gateway = self.gateway
        try:
            if not gateway.config.desktop_enabled:
                raise access.DesktopError("DESKTOP_DISABLED", "Computer is disabled.", 404)
            try:
                claims = verify(gateway.config.desktop_control_key, request.headers.get("authorization", "").removeprefix("Bearer "))
                access.uid(claims["run_id"])
                access.uid(claims["job_id"])
            except (ValueError, KeyError, TypeError):
                raise access.DesktopError("INVALID_CREDENTIAL", "Invalid computer decision credential.", 401) from None
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 2_000_000:
                    raise access.DesktopError("REQUEST_TOO_LARGE", "Decision request is too large.", 413)
            body = json.loads(raw)
            if not isinstance(body, dict):
                raise ValueError("Expected a decision object")
            backend, payload = upstream(body)
            active(claims, await gateway.runtime("GET", "status"))
            key = os.environ.get("RVBBIT_CLOVER_KEY", "")
            if not key:
                raise access.DesktopError("CHOOSER_UNAVAILABLE", "The optional chooser is not configured; continue directly.", 503)
            def source_run():
                with gateway.conn_factory() as conn:
                    return conn.execute("SELECT requester FROM rvbbit.calliope_desktop_runs WHERE id=%s::uuid", (claims["run_id"],)).fetchone()
            run = await asyncio.to_thread(source_run)
            if not run:
                raise access.DesktopError("RUN_NOT_FOUND", "Unknown computer run.", 404)
            request_id = access.uid(body.get("request_id"))
            receipt = {"job_id": claims["job_id"], "generation": claims["generation"],
                       "dispatch_id": str(uuid.uuid4()),
                       "provider": body.get("provider", "jev"), "goal": body["goal"],
                       "options": body["options"], "observation_sha256": hashlib.sha256(body["observation"].encode()).hexdigest(),
                       "request_sha256": hashlib.sha256(raw).hexdigest()}
            # Persist dispatch before sending. No implicit retry after an
            # uncertain response: a caller can reobserve and ask afresh.
            event = await asyncio.to_thread(access.append_event, gateway.conn_factory, claims["run_id"], run["requester"],
                                            "decision.requested", receipt, "decision:" + request_id)
            if event["payload"].get("request_sha256") != receipt["request_sha256"]:
                raise access.DesktopError("IDEMPOTENCY_CONFLICT", "Decision ID has different input.", 409)
            with gateway.conn_factory() as conn:
                previous = conn.execute("SELECT payload FROM rvbbit.calliope_workbench_events WHERE desktop_run_id=%s::uuid AND idempotency_key=%s",
                                        (claims["run_id"], "decision-result:" + request_id)).fetchone()
            if previous:
                result = previous["payload"]["result"]
                return JSONResponse(result, status_code=503 if "error" in result else 200)
            if event["payload"].get("dispatch_id") != receipt["dispatch_id"]:
                raise access.DesktopError("DECISION_PENDING", "This decision was already dispatched. Reobserve before asking afresh.", 409)
            started = time.monotonic()
            base = chooser_base()
            async with httpx.AsyncClient(timeout=180, trust_env=False) as client:
                response = await client.post(base + "/b/" + backend + "/predict", json=payload,
                                             headers={"Authorization": "Bearer " + key})
            elapsed = round((time.monotonic() - started) * 1000, 2)
            if response.is_error:
                result = {"error": {"code": "CHOOSER_UNAVAILABLE", "message": "The optional chooser failed; reobserve or continue directly."},
                          "upstream_status": response.status_code, "elapsed_ms": elapsed}
            else:
                value = response.json()
                output = value["outputs"][0]
                selected = output.get("selected_id") if backend == "computer_decide" else output["answers"]["next_action"]["choice"]
                if selected not in {o["id"] for o in body["options"]}:
                    raise ValueError("Chooser returned an unknown option")
                result = {"selected_id": selected, "provider": body.get("provider", "jev"), "elapsed_ms": elapsed,
                          "output": output, "hutch": value.get("hutch"), "usage": value.get("usage")}
            await asyncio.to_thread(access.append_event, gateway.conn_factory, claims["run_id"], run["requester"],
                                    "decision.completed", {"job_id": claims["job_id"], "request_id": request_id, "result": result},
                                    "decision-result:" + request_id)
            # A late model response after takeover must not become fresh input.
            active(claims, await gateway.runtime("GET", "status"))
            return JSONResponse(result, status_code=503 if "error" in result else 200)
        except access.DesktopError as exc:
            return JSONResponse({"error": {"code": exc.code, "message": str(exc)}}, status_code=exc.status)
        except (ValueError, TypeError, KeyError, IndexError):
            return JSONResponse({"error": {"code": "INVALID_DECISION", "message": "Invalid chooser input or response."}}, status_code=400)
        except Exception:
            return JSONResponse({"error": {"code": "CHOOSER_UNAVAILABLE", "message": "The optional chooser is unavailable; reobserve or continue directly."}}, status_code=503)
