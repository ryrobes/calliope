"""CPU analytics using Clover's installed embedding space and portable sklearn models.

Anomaly pipeline and MLPAutoencoder preserve Clover Zoo's 1.6.1 wire/model
contract. Keep the module name `main` for existing autoencoder pickle references.
"""
import asyncio
import base64
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
import hashlib
import importlib
import io
import json
import math
import os
import pickle
import warnings

import hdbscan
import httpx
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sklearn.base import BaseEstimator
from sklearn.cluster import KMeans
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import IsolationForest
from sklearn.exceptions import InconsistentVersionWarning
from sklearn.neighbors import LocalOutlierFactor
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from threadpoolctl import threadpool_limits

CONTRACT = "clover-cpu-analytics-v1/sklearn-1.6.1"
THREADS = max(1, int(os.getenv("ANALYTICS_THREADS", "2")))
MAX_VALUES = int(os.getenv("ANALYTICS_MAX_VALUES", "10000"))
MAX_CELLS = int(os.getenv("ANALYTICS_MAX_CELLS", "1000000"))
MAX_BLOB = 16 * 1024 * 1024


class MLPAutoencoder(BaseEstimator):
    def __init__(self, hidden_layer_sizes=None, max_iter=300, random_state=42):
        self.hidden_layer_sizes = hidden_layer_sizes
        self.max_iter = max_iter
        self.random_state = random_state

    def fit(self, X, y=None):
        X = np.asarray(X, dtype=float)
        n = X.shape[1]
        bottleneck = max(2, n // 3)
        mid = max(bottleneck + 1, n // 2)
        layers = self.hidden_layer_sizes or ((mid, bottleneck, mid) if n >= 8 else (max(2, n // 2),))
        self.scaler_ = StandardScaler()
        scaled = self.scaler_.fit_transform(X)
        self.mlp_ = MLPRegressor(hidden_layer_sizes=layers, max_iter=self.max_iter,
                                  random_state=self.random_state, early_stopping=False)
        self.mlp_.fit(scaled, scaled)
        self.n_features_in_ = n
        self.resolved_layers_ = tuple(layers)
        return self

    def score_samples(self, X):
        scaled = self.scaler_.transform(np.asarray(X, dtype=float))
        predicted = self.mlp_.predict(scaled).reshape(scaled.shape)
        return -np.mean((scaled - predicted) ** 2, axis=1)

    def predict(self, X):
        scores = -self.score_samples(X)
        return np.where(scores > np.quantile(scores, .9), -1, 1)


# Exact globals used by our supported pipelines. Never resolve arbitrary
# pickle globals, estimator callbacks, or persistent IDs supplied by clients.
# This is compatibility loading for trusted model artifacts, not a sandbox.
ALLOWED_GLOBALS = {
    "numpy.float64", "numpy.float32", "numpy.int64", "numpy.int32", "numpy.str_",
    "numpy.ndarray", "numpy.dtype", "numpy._core.multiarray._reconstruct",
    "numpy.core.multiarray._reconstruct", "numpy._core.multiarray.scalar",
    "numpy.core.multiarray.scalar", "numpy._core.numeric._frombuffer",
    "numpy.core.numeric._frombuffer", "numpy.random._pickle.__randomstate_ctor",
    "numpy.random._pickle.__bit_generator_ctor", "numpy.random._mt19937.MT19937",
    "numpy.random.mtrand.RandomState", "builtins.slice",
    "sklearn.pipeline.Pipeline", "sklearn.compose._column_transformer.ColumnTransformer",
    "sklearn.compose._column_transformer._RemainderColsList",
    "sklearn.preprocessing._encoders.OneHotEncoder",
    "sklearn.preprocessing._function_transformer.FunctionTransformer",
    "sklearn.preprocessing._data.StandardScaler",
    "sklearn.ensemble._iforest.IsolationForest", "sklearn.tree._classes.ExtraTreeRegressor",
    "sklearn.tree._tree.Tree", "sklearn.neighbors._lof.LocalOutlierFactor",
    "sklearn.neighbors._kd_tree.newObj", "sklearn.neighbors._kd_tree.KDTree",
    "sklearn.neighbors._ball_tree.newObj", "sklearn.neighbors._ball_tree.BallTree",
    "sklearn.metrics._dist_metrics.newObj", "sklearn.metrics._dist_metrics.EuclideanDistance64",
    "sklearn.neural_network._multilayer_perceptron.MLPRegressor",
    "sklearn.neural_network._stochastic_optimizers.AdamOptimizer",
}


class ModelReader(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "main" and name == "MLPAutoencoder":
            return MLPAutoencoder
        if module + "." + name not in ALLOWED_GLOBALS:
            raise ValueError("Unsupported model class; use a Clover analytics model")
        return getattr(importlib.import_module(module), name)

    def persistent_load(self, pid):
        raise ValueError("Persistent model references are unsupported")


def decoded(value):
    return json.loads(value) if isinstance(value, str) else value


def matrix(value, *, allow_empty=False):
    rows = decoded(value)
    if not isinstance(rows, list) or (not rows and not allow_empty):
        raise ValueError("X must be a non-empty array of rows")
    if not rows:
        return []
    n = len(rows[0]) if isinstance(rows[0], list) else 0
    if not 1 <= n <= 256 or len(rows) * n > MAX_CELLS:
        raise ValueError(f"X requires 1–256 features and at most {MAX_CELLS} cells")
    for row in rows:
        if not isinstance(row, list) or len(row) != n:
            raise ValueError("X rows must have the same number of features")
        if any(type(v) not in (str, int, float) or (type(v) in (int, float) and not math.isfinite(v)) for v in row):
            raise ValueError("Features must be finite numbers or categorical strings; missing values must be filled first")
    return rows


def prepare(operation, payload):
    payload = decoded(payload)
    if not isinstance(payload, dict):
        raise ValueError("Input must be an object")
    p = dict(payload)
    if operation == "health":
        return {}
    if operation == "cluster":
        p["values"] = decoded(p.get("values"))
        if not isinstance(p["values"], list) or len(p["values"]) > MAX_VALUES:
            raise ValueError(f"values must be an array of at most {MAX_VALUES} strings")
        if any(not isinstance(s, str) or len(s) > 16000 for s in p["values"]):
            raise ValueError("Each value must be text of at most 16000 characters")
        k = p.get("num_clusters")
        if k in (None, "", "null"):
            k = 0
        if isinstance(k, str):
            k = int(k)
        if type(k) is not int or k < 0:
            raise ValueError("num_clusters must be a nonnegative integer; 0 selects HDBSCAN")
        p["num_clusters"] = k
    elif operation in ("anomaly_fit", "anomaly_score"):
        p["X"] = matrix(p.get("X"), allow_empty=operation == "anomaly_score")
        if operation == "anomaly_fit":
            n = len(p["X"][0])
            cat = decoded(p.get("cat_features") or [])
            if not isinstance(cat, list) or any(type(i) is not int or not 0 <= i < n for i in cat) or len(cat) != len(set(cat)):
                raise ValueError("cat_features must contain distinct valid column indices")
            p["cat_features"] = cat
            if any(type(v) not in (int, float) for row in p["X"] for i, v in enumerate(row) if i not in cat):
                raise ValueError("String features must be listed in cat_features")
            names = decoded(p.get("feature_names") or [f"f{i}" for i in range(n)])
            if not isinstance(names, list) or len(names) != n or any(not isinstance(s, str) for s in names):
                raise ValueError("feature_names must match X columns")
            p["feature_names"] = names
            p["algorithm"], _ = detector(p.get("algorithm", "isolation_forest"))
    else:
        raise ValueError("Unknown operation; choose cluster, anomaly_fit or anomaly_score")
    return p


def detector(algorithm):
    aliases = {"": "isolation_forest", "iforest": "isolation_forest", "anomaly": "isolation_forest",
               "local_outlier_factor": "lof", "autoenc": "autoencoder", "mlp_autoencoder": "autoencoder"}
    if not isinstance(algorithm, str):
        raise ValueError("algorithm must be a string")
    name = aliases.get(algorithm.strip().lower(), algorithm.strip().lower())
    builders = {
        "isolation_forest": lambda: IsolationForest(n_estimators=200, contamination="auto", random_state=42, n_jobs=THREADS),
        "lof": lambda: LocalOutlierFactor(n_neighbors=20, contamination="auto", novelty=True, n_jobs=THREADS),
        "autoencoder": MLPAutoencoder,
    }
    if name not in builders:
        raise ValueError("Supported algorithms: isolation_forest, lof, autoencoder")
    return name, builders[name]()


class Models:
    def __init__(self):
        self.cache = OrderedDict()
        self.bytes = 0

    def put(self, sha, model, size):
        old = self.cache.pop(sha, None)
        self.bytes -= old[1] if old else 0
        self.cache[sha] = (model, size)
        self.bytes += size
        while len(self.cache) > 8 or self.bytes > 64 * 1024 * 1024:
            self.bytes -= self.cache.popitem(last=False)[1][1]

    def fit(self, p):
        n = len(p["X"][0])
        cat = p["cat_features"]
        transforms = []
        if cat:
            transforms.append(("cat", OneHotEncoder(handle_unknown="infrequent_if_exist", sparse_output=False,
                                                      max_categories=20, min_frequency=2), cat))
        nums = [i for i in range(n) if i not in cat]
        if nums:
            transforms.append(("num", "passthrough", nums))
        name, estimator = detector(p["algorithm"])
        model = Pipeline([("preproc", ColumnTransformer(transforms, remainder="drop")), ("detector", estimator)])
        model._lars_algo_name = name
        model.fit(np.asarray(p["X"], dtype=object))
        blob = pickle.dumps(model, protocol=pickle.HIGHEST_PROTOCOL)
        if len(blob) > MAX_BLOB:
            raise ValueError("Fitted model exceeds the 16 MiB artifact limit")
        sha = hashlib.sha256(blob).hexdigest()
        self.put(sha, model, len(blob))
        return {"blob_b64": base64.b64encode(blob).decode(), "blob_size_bytes": len(blob), "blob_sha256": sha,
                "algorithm": name, "n_rows": len(p["X"]), "n_features": n, "feature_names": p["feature_names"],
                "metadata": {"base_model": "sklearn/" + name, "cat_feature_indices": cat,
                             "score_convention": "higher_is_more_anomalous", "sklearn_version": "1.6.1", "contract": CONTRACT}}

    def score(self, p):
        if not p["X"]:
            return {"scores": [], "n_rows": 0}
        sha = p.get("model_sha256")
        blob_text = p.get("model_blob_b64")
        model = None
        # Always verify a supplied blob, even when the claimed SHA is cached.
        if blob_text:
            if not isinstance(blob_text, str) or len(blob_text) > (MAX_BLOB * 4 // 3 + 4):
                raise ValueError("Model blob exceeds 16 MiB")
            blob = base64.b64decode(blob_text, validate=True)
            actual = hashlib.sha256(blob).hexdigest()
            if sha and sha != actual:
                raise ValueError("Model blob SHA mismatch")
            sha = actual
            if sha not in self.cache:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", InconsistentVersionWarning)
                    model = ModelReader(io.BytesIO(blob)).load()
                if not isinstance(model, Pipeline) or set(model.named_steps) != {"preproc", "detector"}:
                    raise ValueError("Expected a Clover anomaly pipeline")
                if not isinstance(model.named_steps["detector"], (IsolationForest, LocalOutlierFactor, MLPAutoencoder)):
                    raise ValueError("Unsupported anomaly estimator")
                self.put(sha, model, len(blob))
        if sha not in self.cache:
            raise HTTPException(412, "Model cache miss; retry with model_blob_b64 from the saved SQL artifact")
        self.cache.move_to_end(sha)
        model = self.cache[sha][0]
        scores = -model.score_samples(np.asarray(p["X"], dtype=object))
        if not np.isfinite(scores).all():
            raise ValueError("Model produced nonfinite scores")
        return {"scores": scores.tolist(), "n_rows": len(scores), "algorithm": model._lars_algo_name}


def cluster_vectors(p, vectors):
    values = p["values"]
    if len(values) <= 1:
        assignments = [0] * len(values)
        noise = set()
    elif p["num_clusters"]:
        assignments = KMeans(n_clusters=min(p["num_clusters"], len(values)), n_init="auto", random_state=42).fit_predict(vectors).tolist()
        noise = set()
    else:
        labels = hdbscan.HDBSCAN(min_cluster_size=max(2, len(values) // 50), metric="euclidean").fit_predict(vectors).tolist()
        ids = list(dict.fromkeys(i for i in labels if i >= 0))
        mapping = {old: new for new, old in enumerate(ids)}
        assignments = [mapping.get(i, len(ids)) for i in labels]
        noise = {len(ids)} if -1 in labels else set()
    clusters = []
    for cid in sorted(set(assignments)):
        members = [i for i, a in enumerate(assignments) if a == cid]
        subset = vectors[members]
        representative = members[int(np.argmin(np.linalg.norm(subset - subset.mean(axis=0), axis=1)))]
        clusters.append({"id": cid, "size": len(members), "representative": values[representative],
                         "members": members, "noise": cid in noise})
    return {"clusters": clusters, "assignments": assignments, "num_clusters": len(clusters),
            "algorithm": "kmeans" if p["num_clusters"] else "hdbscan",
            "criterion_applied": False}


class Batch(BaseModel):
    inputs: list[dict] = Field(min_length=1, max_length=8)


class BodyLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        messages, size = [], 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            size += len(message.get("body", b""))
            if size > 32 * 1024 * 1024:
                return await JSONResponse({"detail": "Analytics request exceeds 32 MiB"}, 413)(scope, receive, send)
            messages.append(message)
            if not message.get("more_body"):
                break
        async def replay():
            return messages.pop(0) if messages else await receive()
        await self.app(scope, replay, send)


def create_app(*, transport=None, base_url=None, key=None):
    base_url = (base_url or os.getenv("RVBBIT_CLOVER_BASE_URL", "http://clover:8090")).rstrip("/")
    key = key if key is not None else os.getenv("RVBBIT_CLOVER_KEY", "")
    models = Models()

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=120, trust_env=False, transport=transport) as client:
            app.state.client = client
            app.state.pending = 0
            app.state.slot = asyncio.Semaphore(1)
            # One job at a time avoids races in the model cache / global BLAS limits.
            with threadpool_limits(limits=THREADS):
                yield

    app = FastAPI(title="Clover CPU analytics", lifespan=lifespan)
    app.add_middleware(BodyLimit)

    @app.get("/health")
    async def health():
        return {"status": "ready", "contract": CONTRACT, "device": "cpu",
                "embedding_configured": bool(key), "operations": ["cluster", "anomaly_fit", "anomaly_score"]}

    async def run(operation, p):
        if operation == "health":
            return await health()
        if operation == "cluster":
            vectors, usage, profile = [], {"input_tokens": 0, "cost": 0.0, "already_metered": True}, None
            if p["values"] and not key:
                raise HTTPException(503, "Configure RVBBIT_CLOVER_KEY for the shared embedding gateway")
            # Small sequential batches respect provider capacity; do not introduce a second embedding space.
            remaining = deque(p["values"])
            while remaining:
                chunk, chars = [], 0
                while remaining and len(chunk) < 32 and chars + len(remaining[0]) <= 64000:
                    value = remaining.popleft()
                    chunk.append(value)
                    chars += len(value)
                try:
                    response = await app.state.client.post(base_url + "/b/embed/predict",
                        headers={"Authorization": "Bearer " + key}, json={"inputs": chunk})
                    response.raise_for_status()
                    raw = response.json()
                    got = raw["outputs"]
                    version = raw["hutch"]["model_version"]
                    if profile is not None and version != profile:
                        raise ValueError("Embedding profile changed during clustering")
                    profile = version
                    if len(got) != len(chunk):
                        raise ValueError("Missing embedding vectors")
                    vectors.extend(got)
                    u = raw.get("usage") or {}
                    usage["input_tokens"] += u.get("prompt_tokens", u.get("input_tokens", 0))
                    usage["cost"] += u.get("cost", u.get("cost_usd", 0.0))
                except (httpx.HTTPError, KeyError, TypeError, ValueError):
                    raise HTTPException(502, "Shared embedding request failed; inspect Clover's ledger") from None
            array = np.asarray(vectors, dtype=np.float64)
            if vectors and (array.ndim != 2 or not array.shape[1] or not np.isfinite(array).all()):
                raise HTTPException(502, "Invalid embedding vectors")
            result = await asyncio.to_thread(cluster_vectors, p, array)
            result.update(embedding_profile=profile, usage={**usage, "metered_by": "embed"})
        else:
            result = await asyncio.to_thread(models.fit if operation == "anomaly_fit" else models.score, p)
            result["usage"] = {"cost": 0.0, "compute": "local_cpu"}
        result["contract"] = CONTRACT
        return result

    async def predict(batch, route=None):
        try:
            requests = [(route or item.get("operation"), item.get("payload", item)) for item in batch.inputs]
            prepared = [(op, prepare(op, p)) for op, p in requests]
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid analytics input: check operation, matrix, categories and cluster count") from None
        if app.state.pending >= 4:
            raise HTTPException(429, "CPU analytics worker is busy; retry shortly")
        app.state.pending += 1
        try:
            async with app.state.slot:
                return {"outputs": [await run(op, p) for op, p in prepared]}
        except (ValueError, TypeError, AttributeError, pickle.UnpicklingError, EOFError, InconsistentVersionWarning):
            raise HTTPException(422, "Analytics model/input is incompatible; models require the pinned sklearn 1.6.1 contract") from None
        finally:
            app.state.pending -= 1

    @app.post("/predict")
    async def generic(batch: Batch):
        return await predict(batch)

    @app.post("/specialists/{operation}/predict")
    async def specialized(operation: str, batch: Batch):
        return await predict(batch, operation)

    return app


app = create_app()
