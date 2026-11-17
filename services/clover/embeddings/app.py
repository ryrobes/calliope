"""CPU-only Arctic embeddings using Clover Zoo's model family and normalization."""

from contextlib import asynccontextmanager
import os
import threading
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

MODEL = "Snowflake/snowflake-arctic-embed-l-v2.0"
REVISION = "ac6544c8a46e00af67e330e85a9028c66b8cfd9a"
model = None
lease = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    global model
    import asyncio
    from sentence_transformers import SentenceTransformer
    import torch

    torch.set_num_threads(int(os.environ.get("EMBED_THREADS", "2")))
    model = await asyncio.to_thread(
        SentenceTransformer, MODEL, revision=REVISION, device="cpu"
    )
    yield


app = FastAPI(title="Clover CPU embeddings", lifespan=lifespan)


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: str | list[str]
    model: str | None = None
    encoding_format: str = "float"


@app.get("/health")
def health():
    if model is None:
        raise HTTPException(503, "Model is loading")
    return {
        "ready": True,
        "model": MODEL,
        "revision": REVISION,
        "device": "cpu",
        "dimensions": model.get_sentence_embedding_dimension(),
    }


@app.post("/v1/embeddings")
def embeddings(body: Request):
    texts = [body.input] if isinstance(body.input, str) else body.input
    if not texts or len(texts) > 64 or sum(len(t.encode()) for t in texts) > 262144:
        raise HTTPException(422, "Use 1..64 texts and at most 256 KiB")
    if body.encoding_format != "float" or body.model not in (None, "embed", MODEL):
        raise HTTPException(422, "This worker serves float Arctic embeddings only")
    if model is None:
        raise HTTPException(503, "Model is loading")
    if not lease.acquire(blocking=False):
        raise HTTPException(
            429, "Embedding worker is busy", headers={"Retry-After": "1"}
        )
    try:
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            batch_size=8,
            show_progress_bar=False,
        )
        return {
            "object": "list",
            "model": MODEL,
            "model_revision": REVISION,
            "data": [
                {"object": "embedding", "index": i, "embedding": v.tolist()}
                for i, v in enumerate(vectors)
            ],
        }
    finally:
        lease.release()
