"""Clover's span extraction contract, using a pinned GLiNER medium on CPU."""
from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

MODEL = "urchade/gliner_medium-v2.1"
REVISION = "40ec419335d09393f298636f471328b722c6da9e"
BACKBONE = "microsoft/deberta-v3-base"
BACKBONE_REVISION = "8ccc9b6f36199bec6961081d44eb72fb3f7353f3"
VERSION = f"clover-extract-v1/{MODEL}@{REVISION}/gliner-0.2.16"
model = None
lock = threading.Lock()
load_seconds = None


def load():
    global model, load_seconds
    import torch
    from gliner import GLiNER
    from huggingface_hub import snapshot_download

    torch.set_num_threads(int(os.environ.get("MODEL_THREADS", "2")))
    started = time.monotonic()
    weights = Path(snapshot_download(MODEL, revision=REVISION,
                    allow_patterns=["gliner_config.json", "model.safetensors"]))
    backbone = snapshot_download(BACKBONE, revision=BACKBONE_REVISION,
                                allow_patterns=["*.json", "spm.model"])
    # GLiNER's legacy config names a moving backbone. Resolve its tokenizer and
    # architecture from a pinned local snapshot without modifying cached files.
    config = json.loads((weights / "gliner_config.json").read_text())
    config["model_name"] = backbone
    with tempfile.TemporaryDirectory() as directory:
        prepared = Path(directory)
        (prepared / "gliner_config.json").write_text(json.dumps(config))
        (prepared / "model.safetensors").symlink_to(weights / "model.safetensors")
        model = GLiNER.from_pretrained(directory, map_location="cpu", strict=True).eval()
    load_seconds = round(time.monotonic() - started, 3)


@asynccontextmanager
async def lifespan(app):
    # Fail startup if weights cannot load; health must mean inference is ready.
    load()
    yield


app = FastAPI(lifespan=lifespan)


class Batch(BaseModel):
    inputs: list[dict] = Field(max_length=16)


def normalize(item):
    text = item.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    labels = item.get("labels", [])
    if isinstance(labels, str):
        labels = json.loads(labels) if labels.lstrip().startswith("[") else labels.split(",")
    if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
        raise ValueError("labels must be a list of strings or comma-separated string")
    labels = list(dict.fromkeys(label.strip() for label in labels if label.strip()))
    threshold = float(item.get("threshold", 0.3))
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("threshold must be between 0 and 1")
    if len(labels) > 128 or len(text) > 100000:
        raise ValueError("extract supports at most 128 labels and 100000 characters per input")
    return text, labels, threshold, bool(item.get("flat_ner", True))


@app.get("/health")
def health():
    if model is None:
        raise HTTPException(503, "GLiNER is loading")
    return dict(ok=True, loaded=True, device="cpu", model=MODEL, revision=REVISION,
                backbone_revision=BACKBONE_REVISION, model_version=VERSION, load_seconds=load_seconds)


@app.post("/predict")
@app.post("/specialists/extract/predict")
def predict(batch: Batch):
    try:
        inputs = [normalize(item) for item in batch.inputs]
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc
    health()
    outputs = []
    import torch
    with lock, torch.inference_mode():
        for text, labels, threshold, flat in inputs:
            entities = model.predict_entities(text, labels, threshold=threshold, flat_ner=flat) if text and labels else []
            result = [dict(text=e["text"], label=e["label"], start=int(e["start"]),
                           end=int(e["end"]), score=float(e["score"])) for e in entities]
            outputs.append(sorted(result, key=lambda e: e["score"], reverse=True))
    # Keep each output an array: SQL extract helpers index directly into it.
    return {"outputs": outputs, "usage": {"cost": 0.0}}
