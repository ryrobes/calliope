"""The catalog crawl must not re-embed unchanged meaning or null out good vectors."""
import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "crates" / "pg_rvbbit" / "sql" / "migrations"


def _latest_definition(function):
    pattern = re.compile(rf"CREATE OR REPLACE FUNCTION rvbbit\.{function}\(", re.I)
    defining = sorted(p for p in MIGRATIONS.glob("*.sql") if pattern.search(p.read_text(encoding="utf-8")))
    assert defining, function
    text = defining[-1].read_text(encoding="utf-8")
    start = pattern.search(text).start()
    return defining[-1].name, text[start:text.index("$fn$;", text.index("$fn$", start) + 4)]


def test_crawl_keeps_vectors_whose_meaning_did_not_change():
    """Catalog docs carry live row counts, distinct counts, null shares and sampled
    examples. Re-embedding every doc whose numbers moved cost about an hour per
    crawl on a CPU embedder; such docs keep their vector inside the reuse window."""
    name, body = _latest_definition("_catalog_crawl_one")
    assert body.count("_catalog_reusable_vector(") >= 3, f"{name}: prewarm and both per-doc paths must try reuse"
    _, reusable = _latest_definition("_catalog_reusable_vector")
    assert "_catalog_doc_meaning(o.doc) = rvbbit._catalog_doc_meaning(p_doc)" in reusable
    assert "catalog_vector_reuse_days" in reusable, "reuse must be bounded by an age window"


def test_crawl_never_writes_null_over_a_vector():
    """One refused embed call used to stop embedding for the rest of the table and
    overwrite every remaining doc's vector with NULL."""
    name, body = _latest_definition("_catalog_crawl_one")
    assert body.count("embedding = COALESCE(EXCLUDED.embedding, rvbbit.catalog_docs.embedding)") == 2, name
    assert "embedding = EXCLUDED.embedding" not in body, name
    assert re.search(r"v_fails\s*>=\s*3", body), f"{name}: one failure must not stop the table's embedding"
