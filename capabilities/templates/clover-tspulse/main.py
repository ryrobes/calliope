"""CPU TSPulse worker. Algorithms preserve Clover Zoo's v2 CPU contract."""
from contextlib import asynccontextmanager
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import numpy as np
import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, ValidationError

MODEL_TSPULSE = "ibm-granite/granite-timeseries-tspulse-r1"
ANOMALY_REVISION = "2e64fcdc2a06d3565dfadaf0065c0ab5055f80f2"
RECONSTRUCT_REVISION = "b12164578f7b893ada0028c00d292ba10383d25a"
VERSION = f"clover-tspulse-v2-cpu/{ANOMALY_REVISION}/{RECONSTRUCT_REVISION}/tsfm-0.3.2"
TSPULSE_DEVICE = "cpu"
TSPULSE_CONTEXT_LENGTH = 512
TSPULSE_MIN_ANOMALY_LENGTH = 1536
tspulse_anomaly_model = tspulse_reconstruct_model = tspulse_anomaly_pipeline = None
_tspulse_lock = threading.Lock()
load_seconds = None
log = logging.getLogger(__name__)


def load():
    global tspulse_anomaly_model, tspulse_reconstruct_model, tspulse_anomaly_pipeline, load_seconds
    from tsfm_public.models.tspulse import TSPulseForReconstruction
    from tsfm_public.toolkit.ad_helpers import AnomalyScoreMethods
    from tsfm_public.toolkit.time_series_anomaly_detection_pipeline import TimeSeriesAnomalyDetectionPipeline

    torch.set_num_threads(int(os.environ.get("MODEL_THREADS", "2")))
    started = time.monotonic()
    args = dict(num_input_channels=1, mask_type="user", cache_dir=os.environ.get("HF_HOME"))
    tspulse_anomaly_model = TSPulseForReconstruction.from_pretrained(
        MODEL_TSPULSE, revision=ANOMALY_REVISION, **args).to("cpu").eval()
    tspulse_reconstruct_model = TSPulseForReconstruction.from_pretrained(
        MODEL_TSPULSE, revision=RECONSTRUCT_REVISION, **args).to("cpu").eval()
    tspulse_anomaly_pipeline = TimeSeriesAnomalyDetectionPipeline(
        tspulse_anomaly_model, timestamp_column="timestamp", target_columns=["value"],
        prediction_mode=[AnomalyScoreMethods.PREDICTIVE.value, AnomalyScoreMethods.TIME_RECONSTRUCTION.value,
                         AnomalyScoreMethods.FREQUENCY_RECONSTRUCTION.value],
        aggregation_length=64, aggr_function="max", smoothing_length=8,
        least_significant_scale=0.01, least_significant_score=0.1, device=-1)
    load_seconds = round(time.monotonic() - started, 3)


@asynccontextmanager
async def lifespan(app):
    load()
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    if tspulse_anomaly_pipeline is None or tspulse_reconstruct_model is None:
        raise HTTPException(503, "TSPulse is loading")
    return dict(ok=True, loaded=True, device="cpu", model=MODEL_TSPULSE, model_version=VERSION,
                anomaly_revision=ANOMALY_REVISION, reconstruct_revision=RECONSTRUCT_REVISION,
                load_seconds=load_seconds, window=512, min_anomaly_points=1536)


def _require(*args):
    health()


class TimeSeriesAnomalyRequest(BaseModel):
    values: list[float | None] = Field(min_length=1536, max_length=100000)
    timestamps: list[str] | None = None
    threshold: float = Field(default=0.6, ge=0, le=1)


class TimeSeriesImputeRequest(BaseModel):
    values: list[float | None] = Field(min_length=512, max_length=100000)
    stride: int = Field(default=256, ge=1, le=512)


class TimeSeriesEmbeddingRequest(BaseModel):
    values: list[float | None] = Field(min_length=512, max_length=100000)


class TimeSeriesSimilarityRequest(BaseModel):
    left: list[float | None] = Field(min_length=512, max_length=100000)
    right: list[float | None] = Field(min_length=512, max_length=100000)


def _numeric_series(values: List[Any], field_name: str) -> np.ndarray:
    if not values:
        raise ValueError(f"{field_name} must be non-empty")
    converted: List[float] = []
    for i, value in enumerate(values):
        if value is None:
            converted.append(float("nan"))
            continue
        try:
            converted.append(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"{field_name}[{i}] must be numeric or null, got {value!r}"
            ) from exc
    return np.asarray(converted, dtype=np.float32)


def _tensor_numpy(value: Any) -> np.ndarray:
    if hasattr(value, "detach"):
        value = value.detach()
    if hasattr(value, "cpu"):
        value = value.cpu()
    if hasattr(value, "float"):
        value = value.float()
    return np.asarray(value)


def _tspulse_values(
    values: List[Optional[float]],
    *,
    field_name: str,
    min_length: int,
    allow_missing: bool,
) -> np.ndarray:
    arr = _numeric_series(values, field_name)
    if len(arr) < min_length:
        raise ValueError(
            f"{field_name} requires at least {min_length} points; got {len(arr)}"
        )
    if not allow_missing and not np.isfinite(arr).all():
        raise ValueError(
            f"{field_name} contains null/non-finite points; impute it first"
        )
    if not np.isfinite(arr).any():
        raise ValueError(f"{field_name} must contain at least one observed point")
    return arr


def _window_starts(length: int, window: int, stride: int) -> List[int]:
    if length < window:
        raise ValueError(f"series requires at least {window} points; got {length}")
    if stride < 1 or stride > window:
        raise ValueError(f"stride must be between 1 and {window}")
    starts = list(range(0, length - window + 1, stride))
    last = length - window
    if not starts or starts[-1] != last:
        starts.append(last)
    return starts


def _finite_json(value: Any) -> Optional[float]:
    number = float(value)
    return number if np.isfinite(number) else None


def _tspulse_embedding(values: np.ndarray) -> np.ndarray:
    assert tspulse_reconstruct_model is not None
    from tsfm_public.models.tspulse.utils.helpers import get_embeddings  # type: ignore

    window = values[-TSPULSE_CONTEXT_LENGTH:]
    tensor = torch.tensor(window, dtype=torch.float32).reshape(
        1, TSPULSE_CONTEXT_LENGTH, 1
    ).to(TSPULSE_DEVICE)
    with torch.inference_mode():
        embedding = get_embeddings(
            tspulse_reconstruct_model, tensor, mode="register"
        )
    return _tensor_numpy(embedding).reshape(-1).astype(np.float32)


def timeseries_anomalies(req: TimeSeriesAnomalyRequest) -> Dict[str, Any]:
    _require("tspulse", "TSPulse")
    assert tspulse_anomaly_pipeline is not None
    if not (0.0 <= req.threshold <= 1.0):
        raise HTTPException(status_code=400, detail="threshold must be between 0 and 1")
    try:
        values = _tspulse_values(
            req.values,
            field_name="values",
            min_length=TSPULSE_MIN_ANOMALY_LENGTH,
            allow_missing=False,
        )
        import pandas as pd  # local import keeps base gateway import light

        if req.timestamps is not None:
            if len(req.timestamps) != len(values):
                raise ValueError(
                    f"timestamps has {len(req.timestamps)} values; expected {len(values)}"
                )
            timestamps = pd.to_datetime(req.timestamps, errors="raise")
        else:
            timestamps = pd.date_range(
                "2000-01-01", periods=len(values), freq="s"
            )
        frame = pd.DataFrame({"timestamp": timestamps, "value": values})
        with _tspulse_lock:
            result = tspulse_anomaly_pipeline(
                frame,
                batch_size=256,
                predictive_score_smoothing=True,
            )
        if "anomaly_score" not in result:
            raise RuntimeError("TSPulse result did not contain anomaly_score")
        scores = [_finite_json(value) for value in result["anomaly_score"]]
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("TSPulse anomaly detection failed")
        raise HTTPException(
            status_code=500,
            detail=f"TSPulse anomaly detection failed: {type(e).__name__}: {e}",
        )

    anomalies = [
        bool(score is not None and score >= req.threshold) for score in scores
    ]
    return {
        "model": MODEL_TSPULSE,
        "device": TSPULSE_DEVICE,
        "scores": scores,
        "anomalies": anomalies,
        "anomaly_indices": [i for i, flagged in enumerate(anomalies) if flagged],
        "threshold": float(req.threshold),
        "n_points": len(values),
    }


def timeseries_impute(req: TimeSeriesImputeRequest) -> Dict[str, Any]:
    _require("tspulse", "TSPulse")
    assert tspulse_reconstruct_model is not None
    try:
        values = _tspulse_values(
            req.values,
            field_name="values",
            min_length=TSPULSE_CONTEXT_LENGTH,
            allow_missing=True,
        )
        starts = _window_starts(
            len(values), TSPULSE_CONTEXT_LENGTH, int(req.stride)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    missing = ~np.isfinite(values)
    if not missing.any():
        return {
            "model": MODEL_TSPULSE,
            "device": TSPULSE_DEVICE,
            "values": [float(value) for value in values],
            "imputed_indices": [],
            "n_points": len(values),
            "window": TSPULSE_CONTEXT_LENGTH,
        }

    sums = np.zeros(len(values), dtype=np.float64)
    counts = np.zeros(len(values), dtype=np.int32)
    try:
        with _tspulse_lock, torch.inference_mode():
            for start in starts:
                stop = start + TSPULSE_CONTEXT_LENGTH
                window = values[start:stop]
                observed = np.isfinite(window)
                filled = np.where(observed, window, 0.0).astype(np.float32)
                tensor = torch.tensor(filled).reshape(
                    1, TSPULSE_CONTEXT_LENGTH, 1
                ).to(TSPULSE_DEVICE)
                mask = torch.tensor(observed.astype(np.float32)).reshape(
                    1, TSPULSE_CONTEXT_LENGTH, 1
                ).to(TSPULSE_DEVICE)
                output = tspulse_reconstruct_model(
                    past_values=tensor,
                    past_observed_mask=mask,
                    return_loss=False,
                )
                reconstructed = _tensor_numpy(
                    output.reconstruction_outputs
                )[0, :, 0]
                local_missing = ~observed
                indices = np.arange(start, stop)[local_missing]
                sums[indices] += reconstructed[local_missing]
                counts[indices] += 1
    except Exception as e:
        log.exception("TSPulse imputation failed")
        raise HTTPException(
            status_code=500,
            detail=f"TSPulse imputation failed: {type(e).__name__}: {e}",
        )

    unresolved = np.flatnonzero(missing & (counts == 0))
    if len(unresolved):
        raise HTTPException(
            status_code=500,
            detail=f"TSPulse left {len(unresolved)} missing points outside inference windows",
        )
    imputed = values.copy()
    imputed[missing] = sums[missing] / counts[missing]
    return {
        "model": MODEL_TSPULSE,
        "device": TSPULSE_DEVICE,
        "values": [float(value) for value in imputed],
        "imputed_indices": np.flatnonzero(missing).astype(int).tolist(),
        "n_points": len(values),
        "window": TSPULSE_CONTEXT_LENGTH,
        "stride": int(req.stride),
    }


def timeseries_embedding(req: TimeSeriesEmbeddingRequest) -> Dict[str, Any]:
    _require("tspulse", "TSPulse")
    try:
        values = _tspulse_values(
            req.values,
            field_name="values",
            min_length=TSPULSE_CONTEXT_LENGTH,
            allow_missing=False,
        )
        with _tspulse_lock:
            embedding = _tspulse_embedding(values)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("TSPulse embedding failed")
        raise HTTPException(
            status_code=500,
            detail=f"TSPulse embedding failed: {type(e).__name__}: {e}",
        )
    return {
        "model": MODEL_TSPULSE,
        "device": TSPULSE_DEVICE,
        "embedding": [float(value) for value in embedding],
        "dimension": int(len(embedding)),
        "n_points": len(values),
        "window_start": len(values) - TSPULSE_CONTEXT_LENGTH,
        "window": TSPULSE_CONTEXT_LENGTH,
    }


def timeseries_similarity(req: TimeSeriesSimilarityRequest) -> Dict[str, Any]:
    _require("tspulse", "TSPulse")
    try:
        left = _tspulse_values(
            req.left,
            field_name="left",
            min_length=TSPULSE_CONTEXT_LENGTH,
            allow_missing=False,
        )
        right = _tspulse_values(
            req.right,
            field_name="right",
            min_length=TSPULSE_CONTEXT_LENGTH,
            allow_missing=False,
        )
        with _tspulse_lock:
            left_embedding = _tspulse_embedding(left)
            right_embedding = _tspulse_embedding(right)
        denominator = float(
            np.linalg.norm(left_embedding) * np.linalg.norm(right_embedding)
        )
        similarity = (
            float(np.dot(left_embedding, right_embedding) / denominator)
            if denominator > 0
            else 0.0
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        log.exception("TSPulse similarity failed")
        raise HTTPException(
            status_code=500,
            detail=f"TSPulse similarity failed: {type(e).__name__}: {e}",
        )
    return {
        "model": MODEL_TSPULSE,
        "device": TSPULSE_DEVICE,
        "similarity": similarity,
        "left_points": len(left),
        "right_points": len(right),
        "window": TSPULSE_CONTEXT_LENGTH,
    }


OPERATIONS = {
    "timeseries_anomalies": (TimeSeriesAnomalyRequest, timeseries_anomalies),
    "timeseries_impute": (TimeSeriesImputeRequest, timeseries_impute),
    "timeseries_embed": (TimeSeriesEmbeddingRequest, timeseries_embedding),
    "timeseries_similarity": (TimeSeriesSimilarityRequest, timeseries_similarity),
}


class Batch(BaseModel):
    inputs: list[dict] = Field(max_length=8)


def prepare(operation, payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    if operation == "health":
        return None, None
    if operation not in OPERATIONS:
        raise ValueError("Unknown TSPulse operation: " + str(operation))
    payload = dict(payload)
    for name in ("values", "left", "right", "timestamps"):
        if isinstance(payload.get(name), str):
            payload[name] = json.loads(payload[name])
    schema, function = OPERATIONS[operation]
    return function, schema.model_validate(payload)


def predict_batch(batch, operation=None):
    try:
        requests = [prepare(operation or item.get("operation"),
                            item if operation else item.get("payload", {})) for item in batch.inputs]
    except (ValueError, TypeError, ValidationError) as exc:
        # ValidationError text can contain the entire series. Return a compact
        # actionable error without echoing SQL inputs into logs or receipts.
        if isinstance(exc, ValidationError):
            errors = exc.errors(include_input=False, include_url=False)
            raise HTTPException(422, [{"field": list(e["loc"]), "message": e["msg"]} for e in errors]) from exc
        raise HTTPException(422, str(exc)) from exc
    outputs = []
    for function, request in requests:
        result = health() if function is None else function(request)
        result["model_version"] = VERSION
        result["usage"] = {"cost_microusd": 0, "provider": "local-cpu"}
        outputs.append(result)
    return {"outputs": outputs}


@app.post("/predict")
def predict(batch: Batch):
    return predict_batch(batch)


@app.post("/specialists/{operation}/predict")
def specialist(operation: str, batch: Batch):
    return predict_batch(batch, operation)
