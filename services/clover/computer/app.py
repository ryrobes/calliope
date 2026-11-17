"""One GPU scorer behind Hutch. This service never executes computer actions."""

from contextlib import asynccontextmanager
import gc
import json
import logging
import os
import secrets
import threading
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from models import VERSION
from scorer import Scorer


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=100)
    role: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=1000)
    action: str = Field(min_length=1, max_length=100)
    entity_id: str | None = Field(default=None, max_length=100)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text", "multimodal"] = "text"
    goal: str = Field(min_length=1, max_length=4000)
    app: str = Field(min_length=1, max_length=100)
    task_family: str = Field(default="office", max_length=100)
    observation: str | None = Field(default=None, max_length=40000)
    screenshot: str | None = Field(default=None, max_length=6_000_000)
    options: list[Candidate] = Field(min_length=2, max_length=26)

    @model_validator(mode="after")
    def valid_observation(self):
        if self.modality == "text" and not self.observation:
            raise ValueError("Text decisions require an observation")
        if self.modality == "multimodal" and not self.screenshot:
            raise ValueError("Multimodal decisions require a base64 screenshot")
        if len({item.id for item in self.options}) != len(self.options):
            raise ValueError("Candidate IDs must be unique")
        return self


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: list[Decision] = Field(min_length=1, max_length=4)


class Runtime:
    def __init__(self, factory=Scorer):
        self.factory = factory
        self.lock = threading.Lock()
        self.model = None
        self.last_used = 0
        self.state = "unloaded"
        self.stop = threading.Event()

    def unload(self):
        self.model = None
        gc.collect()
        import torch

        torch.cuda.empty_cache()
        self.state = "unloaded"

    def run(self, batch):
        if not self.lock.acquire(blocking=False):
            raise HTTPException(
                429, "Computer decision worker is busy", headers={"Retry-After": "1"}
            )
        try:
            outputs = []
            for item in batch.inputs:
                cold = not self.model or self.model.modality != item.modality
                if cold:
                    if self.model:
                        self.unload()
                    self.state = "loading"
                    self.model = self.factory(item.modality)
                self.state = "scoring"
                value = self.model.score(item)
                value["metadata"]["load_ms"] = self.model.load_ms if cold else 0
                outputs.append(value)
            return {"outputs": outputs}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except HTTPException:
            raise
        except Exception as exc:
            logging.exception("Computer scorer failed: %s", type(exc).__name__)
            self.unload()
            raise HTTPException(
                503, "Scorer unavailable; direct computer use remains available"
            ) from None
        finally:
            self.last_used = time.monotonic()
            if self.model:
                self.state = "loaded"
            self.lock.release()


runtime = Runtime()


@asynccontextmanager
async def lifespan(app):
    def reap():
        while not runtime.stop.wait(30):
            if runtime.lock.acquire(blocking=False):
                try:
                    if runtime.model and time.monotonic() - runtime.last_used > 900:
                        runtime.unload()
                finally:
                    runtime.lock.release()

    thread = threading.Thread(target=reap, daemon=True)
    thread.start()
    yield
    runtime.stop.set()
    thread.join(timeout=2)


app = FastAPI(title="Clover Computer Decisions", lifespan=lifespan)


@app.middleware("http")
async def private_upstream(request: Request, call_next):
    if request.url.path != "/health":
        token = os.environ.get("CLOVER_COMPUTER_KEY", "")
        supplied = request.headers.get("authorization", "")
        if not token or not secrets.compare_digest(supplied, "Bearer " + token):
            return JSONResponse(
                {"detail": "Use the authenticated Hutch endpoint"}, status_code=401
            )
    if int(request.headers.get("content-length") or 0) > 8_000_000:
        return JSONResponse({"detail": "Request too large"}, status_code=413)
    return await call_next(request)


def cuda_available():
    try:
        import torch
        return torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    except (ImportError, RuntimeError):
        return False


def weights_present(root):
    required = ["base/config.json", "base/tokenizer_config.json"]
    for modality in ("text", "multimodal"):
        required.extend([f"adapters/{modality}/adapter_config.json", f"adapters/{modality}/adapter_model.safetensors"])
    index = root / "base/model.safetensors.index.json"
    try:
        if index.is_file():
            shards = set(json.loads(index.read_text())["weight_map"].values())
            if not shards:
                return False
            required.extend("base/" + shard for shard in shards)
        else:
            required.append("base/model.safetensors")
        return all((root / path).is_file() and (root / path).stat().st_size > 0 for path in required)
    except (OSError, ValueError, KeyError, TypeError):
        return False


@app.get("/health")
def health():
    from models import ROOT

    available = weights_present(ROOT)
    gpu = cuda_available()
    credential = bool(os.environ.get("CLOVER_COMPUTER_KEY"))
    ready = available and gpu and credential
    value = {
        "ok": ready,
        "state": runtime.state,
        "model_version": VERSION,
        "weights_present": available,
        "credential_present": credential,
        "cuda_available": gpu,
        "inference_verified": False,
        "modality": runtime.model.modality if runtime.model else None,
    }
    return JSONResponse(
        value, status_code=200 if ready else 503
    )


@app.post("/predict")
def predict(body: Batch):
    return runtime.run(body)
