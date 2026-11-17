"""Install the immutable, pre-enriched Hermes reference bundle into RVBBIT.

Release tooling performs crawling, chunking, enrichment and default embeddings.
Installations using another embedding profile regenerate the vectors through
their configured SQL embedder before transactionally importing the bundle.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1] if len(_HERE.parents) > 1 else _HERE
_MIGRATION = (
    _HERE / "sql/0312_hermes_docs_store.sql"
    if (_HERE / "sql/0312_hermes_docs_store.sql").exists()
    else _REPO / "crates/pg_rvbbit/sql/migrations/0312_hermes_docs_store.sql"
)
_DOCS_DIR = (
    _HERE / "hermes-docs"
    if (_HERE / "hermes-docs").exists()
    else _REPO / "docker/calliope/hermes-docs"
)
_BUNDLE = _DOCS_DIR / "hermes-docs.bundle.json.gz"
_SOURCE = _DOCS_DIR / "source.json"
_FORMAT = "calliope.hermes-docs.v1"


def read_bundle(path: Path = _BUNDLE, source_path: Path = _SOURCE) -> dict[str, Any]:
    source = json.loads(source_path.read_text(encoding="utf-8"))
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("manifest"), dict):
        raise RuntimeError("Hermes docs bundle is not an object with a manifest")
    manifest = payload["manifest"]
    if manifest.get("format") != _FORMAT:
        raise RuntimeError(f"unsupported Hermes docs bundle format: {manifest.get('format')}")
    if manifest.get("hermes_revision") != source.get("revision"):
        raise RuntimeError("Hermes docs revision does not match the pinned Hermes source")
    if manifest.get("hermes_image_digest") != source.get("image_digest"):
        raise RuntimeError("Hermes docs image digest does not match the pinned Hermes image")

    documents = payload.get("documents")
    chunks = payload.get("chunks")
    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not all(isinstance(items, list) for items in (documents, chunks, nodes, edges)):
        raise RuntimeError("Hermes docs bundle collections are malformed")
    expected_counts = {
        "document_count": len(documents),
        "chunk_count": len(chunks),
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
    for key, actual in expected_counts.items():
        if int(manifest.get(key, -1)) != actual:
            raise RuntimeError(f"Hermes docs {key} does not match the manifest")
    dimensions = int(manifest.get("embedding_dimensions") or 0)
    if dimensions <= 0 or not chunks:
        raise RuntimeError("Hermes docs shipping bundle has no embeddings")
    for chunk in chunks:
        vector = chunk.get("embedding") if isinstance(chunk, dict) else None
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise RuntimeError("Hermes docs chunk embedding dimensions are inconsistent")
    return payload


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, separators=(",", ":"))


def install(conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Import one validated bundle and atomically make it the active corpus."""
    # A shipped reference corpus must use the customer's selected space too.
    # The ordinary default ships precomputed; custom models reuse the normal
    # SQL embedding cache rather than creating a separate provider connection.
    if conn.execute("SELECT to_regclass('rvbbit.inference_settings') AS registry").fetchone()["registry"]:
        row = conn.execute("SELECT profile FROM rvbbit.inference_settings WHERE singleton").fetchone()
        profile = row["profile"] if row else None
        if profile and (payload["manifest"]["embedding_model"] != profile["embedding_model"]
                        or payload["manifest"]["embedding_dimensions"] != profile["embedding_dimensions"]):
            identity = hashlib.sha256(json.dumps({k:v for k,v in profile.items() if k.startswith("embedding_")}, sort_keys=True).encode()).hexdigest()[:12]
            manifest = {**payload["manifest"], "bundle_id": payload["manifest"]["bundle_id"] + "-" + identity,
                        "embedding_model":profile["embedding_model"], "embedding_dimensions":profile["embedding_dimensions"]}
            current = conn.execute("SELECT bundle_id FROM rvbbit.hermes_doc_bundles WHERE bundle_id=%s AND active", (manifest["bundle_id"],)).fetchone()
            if current:
                return {**manifest,"installed":False,"reason":"already_active"}
            titles = {d["doc_id"]:d["title"] for d in payload["documents"]}
            chunks = []
            for offset in range(0,len(payload["chunks"]),64):
                batch = payload["chunks"][offset:offset+64]
                texts = [titles[c["doc_id"]] + "\n" + c["heading"] + "\n" + c["text"] for c in batch]
                conn.execute("SELECT rvbbit.embed_batch(%s::text[])",(texts,))
                vectors = conn.execute("SELECT rvbbit.embed(t) AS embedding FROM unnest(%s::text[]) t",(texts,)).fetchall()
                if len(vectors)!=len(batch) or any(len(v["embedding"])!=profile["embedding_dimensions"] for v in vectors):
                    raise RuntimeError("Hermes docs embedding profile mismatch")
                chunks.extend({**c,"embedding":v["embedding"]} for c,v in zip(batch,vectors,strict=True))
            payload = {**payload,"manifest":manifest,"chunks":chunks}
    manifest = payload["manifest"]
    bundle_id = str(manifest["bundle_id"])
    current = conn.execute(
        "SELECT bundle_id,content_hash,active FROM rvbbit.hermes_doc_bundles "
        "WHERE bundle_id=%s",
        (bundle_id,),
    ).fetchone()
    if current and current.get("active") and current.get("content_hash") == manifest["content_hash"]:
        return {**manifest, "installed": False, "reason": "already_active"}

    conn.execute(
        "INSERT INTO rvbbit.hermes_doc_bundles "
        "(bundle_id,hermes_revision,hermes_image_digest,embedding_model,"
        "embedding_dimensions,content_hash,manifest,active) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,false) "
        "ON CONFLICT (bundle_id) DO UPDATE SET "
        "hermes_revision=excluded.hermes_revision,"
        "hermes_image_digest=excluded.hermes_image_digest,"
        "embedding_model=excluded.embedding_model,"
        "embedding_dimensions=excluded.embedding_dimensions,"
        "content_hash=excluded.content_hash,manifest=excluded.manifest",
        (
            bundle_id,
            manifest["hermes_revision"],
            manifest["hermes_image_digest"],
            manifest["embedding_model"],
            int(manifest["embedding_dimensions"]),
            manifest["content_hash"],
            _json(manifest),
        ),
    )
    # A matching bundle_id is immutable in release artifacts. Replacing its
    # children also recovers cleanly if a manually interrupted import existed.
    conn.execute("DELETE FROM rvbbit.hermes_docs WHERE bundle_id=%s", (bundle_id,))
    # Chunks cascade from docs and edges from nodes, but nodes hang off the
    # bundle row alone: without this, a retry after an interrupted install
    # fails on the nodes the earlier attempt already committed.
    conn.execute("DELETE FROM rvbbit.hermes_doc_nodes WHERE bundle_id=%s", (bundle_id,))
    with conn.cursor() as cursor:
        cursor.executemany(
            "INSERT INTO rvbbit.hermes_docs "
            "(bundle_id,doc_id,source_kind,path,title,body,metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)",
            [
            (
                bundle_id,
                item["doc_id"],
                item["source_kind"],
                item["path"],
                item["title"],
                item["body"],
                _json(item.get("metadata")),
            )
                for item in payload["documents"]
            ],
        )
        cursor.executemany(
            "INSERT INTO rvbbit.hermes_doc_chunks "
            "(bundle_id,chunk_id,doc_id,ordinal,heading,text,embedding,token_est,metadata) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s::real[],%s,%s::jsonb)",
            [
            (
                bundle_id,
                item["chunk_id"],
                item["doc_id"],
                int(item["ordinal"]),
                item.get("heading") or "",
                item["text"],
                item["embedding"],
                int(item.get("token_est") or 0),
                _json(item.get("metadata")),
            )
                for item in payload["chunks"]
            ],
        )
        cursor.executemany(
            "INSERT INTO rvbbit.hermes_doc_nodes "
            "(bundle_id,node_id,kind,label,properties) VALUES (%s,%s,%s,%s,%s::jsonb)",
            [
            (bundle_id, item["node_id"], item["kind"], item["label"], _json(item.get("properties")))
                for item in payload["nodes"]
            ],
        )
        cursor.executemany(
            "INSERT INTO rvbbit.hermes_doc_edges "
            "(bundle_id,edge_id,source_node_id,target_node_id,relation,properties) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb)",
            [
            (
                bundle_id,
                item["edge_id"],
                item["source_node_id"],
                item["target_node_id"],
                item["relation"],
                _json(item.get("properties")),
            )
                for item in payload["edges"]
            ],
        )
    # Two statements: one UPDATE that flips both rows checks the one-active
    # unique index row by row, and fails whenever it reaches the new bundle
    # before the old one (every upgrade has an active older bundle).
    conn.execute(
        "UPDATE rvbbit.hermes_doc_bundles SET active=false WHERE active AND bundle_id<>%s",
        (bundle_id,),
    )
    conn.execute(
        "UPDATE rvbbit.hermes_doc_bundles SET active=true WHERE bundle_id=%s",
        (bundle_id,),
    )
    return {**manifest, "installed": True}


def ensure(conn_factory: Callable[..., Any]) -> dict[str, Any]:
    """Create the service-owned shape and install the checked-in bundle."""
    payload = read_bundle()
    ddl = _MIGRATION.read_text(encoding="utf-8")
    with conn_factory() as conn:
        conn.execute(ddl)
        return install(conn, payload)
