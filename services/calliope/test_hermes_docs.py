import copy
import gzip
import json

import pytest

import hermes_docs


def test_shipping_bundle_is_prebuilt_pinned_and_embedded():
    bundle = hermes_docs.read_bundle()
    manifest = bundle["manifest"]
    assert manifest["document_count"] == 124
    assert manifest["chunk_count"] == 1192
    assert manifest["node_count"] == 6840
    assert manifest["edge_count"] == 16967
    assert manifest["embedding_dimensions"] == 1024
    assert manifest["embedding_model"] == "voyageai/voyage-4"
    assert {doc["source_kind"] for doc in bundle["documents"]} == {
        "upstream",
        "calliope_overlay",
    }


def test_bundle_dimension_validation_rejects_release_drift(tmp_path):
    bundle = hermes_docs.read_bundle()
    broken = copy.deepcopy(bundle)
    broken["chunks"][0]["embedding"] = [0.0]
    bundle_path = tmp_path / "bundle.json.gz"
    with gzip.open(bundle_path, "wt", encoding="utf-8") as handle:
        json.dump(broken, handle)
    with pytest.raises(RuntimeError, match="dimensions"):
        hermes_docs.read_bundle(bundle_path, hermes_docs._SOURCE)


def test_custom_profile_reembeds_instead_of_importing_foreign_vectors():
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None
        def fetchall(self): return self.rows

    class StopAfterEmbedding(Exception): pass

    class Connection:
        def execute(self, query, args=None):
            if "to_regclass" in query: return Result([{"registry": True}])
            if "SELECT profile" in query:
                return Result([{"profile": {"embedding_model": "new/model", "embedding_dimensions": 2}}])
            if "SELECT bundle_id" in query: return Result([])
            if "embed_batch" in query:
                assert args == (["Title\nSection\nBody"],)
                return Result([])
            if "AS embedding" in query: return Result([{"embedding": [0.25, 0.75]}])
            if "INSERT INTO rvbbit.hermes_doc_bundles" in query:
                assert args[0].startswith("original-")
                assert args[3:5] == ("new/model", 2)
                raise StopAfterEmbedding()
            raise AssertionError(query)

    payload = {"manifest": {"bundle_id": "original", "embedding_model": "old/model",
               "embedding_dimensions": 2, "hermes_revision": "rev", "hermes_image_digest": "digest", "content_hash": "hash"},
               "documents": [{"doc_id": "doc", "title": "Title"}],
               "chunks": [{"doc_id": "doc", "heading": "Section", "text": "Body", "embedding": [1, 0]}]}
    with pytest.raises(StopAfterEmbedding):
        hermes_docs.install(Connection(), payload)
    assert payload["chunks"][0]["embedding"] == [1, 0]


def test_upgrade_install_clears_leftovers_and_activates_in_two_steps():
    """An older active bundle exists and an earlier attempt left nodes behind."""
    statements = []

    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchone(self): return self.rows[0] if self.rows else None

    class Cursor:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def executemany(self, query, rows): statements.append((query, list(rows)))

    class Connection:
        def execute(self, query, args=None):
            statements.append((query, args))
            if "to_regclass" in query: return Result([{"registry": False}])
            if "SELECT bundle_id,content_hash,active" in query: return Result([])
            return Result([])
        def cursor(self): return Cursor()

    payload = {"manifest": {"bundle_id": "new", "embedding_model": "m", "embedding_dimensions": 2,
               "hermes_revision": "rev", "hermes_image_digest": "digest", "content_hash": "hash"},
               "documents": [], "chunks": [], "nodes": [], "edges": []}
    hermes_docs.install(Connection(), payload)
    sql = [q for q, _ in statements]
    nodes_cleared = next(i for i, q in enumerate(sql) if q.startswith("DELETE FROM rvbbit.hermes_doc_nodes"))
    first_insert = next(i for i, q in enumerate(sql) if "INSERT INTO rvbbit.hermes_doc_nodes" in q)
    assert nodes_cleared < first_insert
    flips = [(q, a) for q, a in statements if q.startswith("UPDATE rvbbit.hermes_doc_bundles SET active")]
    assert [q.split("SET ")[1].split(" WHERE")[0] for q, _ in flips] == ["active=false", "active=true"]
    assert all(a == ("new",) for _, a in flips)
