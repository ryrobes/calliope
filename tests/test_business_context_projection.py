"""Business Context search projection — embedding preservation and repair.

Migration 0322 fixed a lane where the frequent no-embed pre-warm
(``business_context_crawl(false)``) overwrote every projection vector with
NULL and nothing ever embedded the documents again, so semantic retrieval
returned nothing while the projection reported itself fresh.

These tests exercise the SQL contract directly with a synthetic projection
document so they do not depend on a Value Graph proposal existing, and they
seed the vector by hand so they do not depend on which embedder the bench
instance has configured (``embed_missing`` is only asserted for shape and
for convergence when the embedder is actually reachable).
"""
from __future__ import annotations

import json
import uuid

import pytest


def _installed(rvbbit) -> bool:
    row = rvbbit.execute(
        "SELECT to_regprocedure('rvbbit.business_context_embed_missing(integer)') IS NOT NULL AS ok"
    ).fetchone()
    return bool(row and row[0])


@pytest.fixture
def projection_doc(rvbbit):
    """Insert one synthetic Business Context projection document; clean up after."""
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0322_business_context_embedding_repair")
    handle = f"value_system:test-{uuid.uuid4().hex[:12]}"
    label = f"Test Value System {handle[-12:]}"
    doc = f"Business value system: {label}. Domain: test. Status: proposed."
    result = rvbbit.execute(
        "SELECT rvbbit._business_context_index_node("
        "'business_value_system', %s, %s, %s::jsonb, %s, false)",
        (label, handle, json.dumps({"status": "proposed", "test": True}), doc),
    ).fetchone()[0]
    node_id = int(result["node_id"])
    yield {"node_id": node_id, "handle": handle, "label": label, "doc": doc}
    rvbbit.execute(
        "DELETE FROM rvbbit.catalog_docs WHERE graph_id='business_context' AND node_id=%s",
        (node_id,),
    )
    rvbbit.execute(
        "DELETE FROM rvbbit.kg_nodes WHERE graph_id='business_context' AND node_id=%s",
        (node_id,),
    )


def _embedding_state(rvbbit, node_id: int):
    return rvbbit.execute(
        "SELECT embedding IS NOT NULL AS embedded, embedded_at IS NOT NULL AS stamped, doc "
        "FROM rvbbit.catalog_docs WHERE graph_id='business_context' AND node_id=%s",
        (node_id,),
    ).fetchone()


def test_no_embed_crawl_preserves_existing_vector_for_unchanged_text(rvbbit, projection_doc):
    node_id = projection_doc["node_id"]
    # Seed a vector by hand (the crawl itself was called with p_do_embed=false).
    rvbbit.execute(
        "UPDATE rvbbit.catalog_docs SET embedding=%s::real[], embedded_at=now() "
        "WHERE graph_id='business_context' AND node_id=%s",
        ([0.1, 0.2, 0.3], node_id),
    )
    assert _embedding_state(rvbbit, node_id)[0] is True

    # A second no-embed pass over the same text must keep the vector.
    result = rvbbit.execute(
        "SELECT rvbbit._business_context_index_node("
        "'business_value_system', %s, %s, '{\"status\":\"proposed\"}'::jsonb, %s, false)",
        (projection_doc["label"], projection_doc["handle"], projection_doc["doc"]),
    ).fetchone()[0]
    assert result["embedded"] is True
    embedded, stamped, _ = _embedding_state(rvbbit, node_id)
    assert embedded is True and stamped is True


def test_no_embed_crawl_invalidates_vector_when_text_changes(rvbbit, projection_doc):
    node_id = projection_doc["node_id"]
    rvbbit.execute(
        "UPDATE rvbbit.catalog_docs SET embedding=%s::real[], embedded_at=now() "
        "WHERE graph_id='business_context' AND node_id=%s",
        ([0.1, 0.2, 0.3], node_id),
    )
    changed = projection_doc["doc"] + " Metrics: order value."
    result = rvbbit.execute(
        "SELECT rvbbit._business_context_index_node("
        "'business_value_system', %s, %s, '{\"status\":\"proposed\"}'::jsonb, %s, false)",
        (projection_doc["label"], projection_doc["handle"], changed),
    ).fetchone()[0]
    assert result["embedded"] is False
    embedded, stamped, doc = _embedding_state(rvbbit, node_id)
    assert embedded is False and stamped is False
    assert doc == changed
    # ...and that unembedded row is exactly what the repair lane looks for.
    assert rvbbit.execute("SELECT rvbbit.business_context_embed_pending()").fetchone()[0] is True


def test_embed_missing_is_bounded_and_reports_convergence(rvbbit, projection_doc):
    node_id = projection_doc["node_id"]
    assert _embedding_state(rvbbit, node_id)[0] is False
    assert rvbbit.execute("SELECT rvbbit.business_context_embed_pending()").fetchone()[0] is True

    result = rvbbit.execute(
        "SELECT rvbbit.business_context_embed_missing(50)"
    ).fetchone()[0]
    assert result["status"] in {"embedded", "error", "busy", "complete"}
    assert set(result) >= {"status", "attempted", "embedded", "failed", "remaining"}
    if result["status"] == "embedded":
        # The configured embedder is reachable: the projection converged.
        assert result["failed"] == 0 or result["embedded"] > 0
        pending_after = rvbbit.execute(
            "SELECT rvbbit.business_context_embed_pending()"
        ).fetchone()[0]
        state = rvbbit.execute(
            "SELECT node_count, embedded_count FROM rvbbit.business_context_state WHERE singleton"
        ).fetchone()
        assert state[1] <= state[0]
        if result["remaining"] == 0:
            assert pending_after is False
            assert state[0] == state[1]
    else:
        # No embedder in this bench: the call must still be safe and honest.
        assert result["embedded"] == 0
        assert _embedding_state(rvbbit, node_id)[0] is False


def test_maintenance_tick_reports_embed_lane(rvbbit):
    if not _installed(rvbbit):
        pytest.skip("run rvbbit.migrate() to install 0322_business_context_embedding_repair")
    result = rvbbit.execute("SELECT rvbbit.business_context_maintenance_tick()").fetchone()[0]
    if result.get("status") == "busy":
        pytest.skip("maintenance lane held by another session")
    assert result["status"] == "complete"
    assert "search" in result and "steward" in result
    assert "embed" in result, "0322 tick must report the bounded embedding repair"
    assert result["embed"]["status"] in {"complete", "embedded", "error", "busy"}
