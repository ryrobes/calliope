"""search_data blends Business Context and catalog hits by *real* relevance.

Regression: business_context_search scores are rank-normalized (the best hit
of any query is 1.0) and the dense floor is permissive, so once the projection
was actually embedded, six unrelated business metrics outranked
public.traffic_violations for "traffic stops police citations". Business hits
now need an absolute cosine above the floor (or a lexical hit) to keep the top
tier; otherwise they sort after tables.
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


@pytest.mark.parametrize("schema", [None, "sales"])
def test_data_search_survives_unavailable_business_context(monkeypatch, offline_grounding, schema):
    hits = [
        _table("sales", "orders", 0.8, 1),
        _table("sales", "order_total", 0.7, 2) | {"kind": "metric"},
        _table("sales", "daily_orders", 0.6, 3) | {"kind": "cube"},
        _table("rvbbit", "private_metadata", 1.0, 4),
        _table("marketing", "campaigns", 0.5, 5),
    ]
    calls = []

    class CatalogConnection:
        def execute(self, sql, params):
            assert "FROM rvbbit.search_data_weighted(" in sql
            calls.append(params)
            return self

        def fetchall(self):
            return hits

    @contextlib.contextmanager
    def catalog_connection():
        yield CatalogConnection()

    def broken_context(*args, **kwargs):
        pytest.fail("Catalog discovery must not invoke Business Context")

    monkeypatch.setattr(server, "_conn", catalog_connection)
    monkeypatch.setattr(server, "_ensure_business_context_fresh", broken_context)
    monkeypatch.setattr(server, "_business_context_relevance", broken_context)
    result = server.tool_search_data("orders", limit=8, schema=schema)
    expected = ["sales.order_total", "sales.daily_orders", "sales.orders"]
    if schema is None:
        expected.append("marketing.campaigns")
    assert [match["object"] for match in result["matches"]] == expected
    assert all("samples" in match for match in result["matches"])
    assert all("business_context" not in match for match in result["matches"])
    assert calls == [("orders", 32, None, server.GRAPH)]
    assert result["note"] is None


class _Cur:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _RO:
    def cursor(self):
        return _Cur()


@pytest.fixture
def offline_grounding(monkeypatch):
    @contextlib.contextmanager
    def _ro():
        yield _RO()

    monkeypatch.setattr(server, "_ro", _ro)
    monkeypatch.setattr(server, "_schema_allowed", lambda schema: schema not in {"rvbbit"})
    monkeypatch.setattr(server, "_samples", lambda cur, s, r, n=5: [])
    monkeypatch.setattr(server, "_col_stats", lambda cur, s, r, **_kw: None)
    monkeypatch.setattr(server, "_freshness", lambda cur, s, r: None)


def _table(schema, rel, score, node_id):
    return {"node_id": node_id, "kind": "db_table", "schema_name": schema, "rel_name": rel,
            "col_name": None, "score": score, "boosted_score": score, "doc": f"{schema}.{rel}",
            "usage_touches": 0}


def _business(handle, score, node_id, kind="business_metric"):
    return {"node_id": node_id, "kind": kind, "schema_name": None, "rel_name": handle,
            "col_name": None, "score": score, "boosted_score": score, "doc": handle,
            "usage_touches": 0, "properties": {"status": "promoted"}}


def test_weak_business_hits_sort_after_tables(offline_grounding):
    hits = [
        _table("public", "traffic_violations", 1.0, 1),
        _table("public", "traffic_stops_geo", 0.7, 2),
        _business("contact_lifecycle_progression_count", 1.0, 100),
        _business("customer_order_count", 0.9, 101),
    ]
    relevance = {100: {"cosine": 0.22, "lexical": False}, 101: {"cosine": 0.16, "lexical": False}}
    out = server._blend_search_hits(hits, relevance, limit=3)
    assert [m["object"] for m in out] == [
        "public.traffic_violations", "public.traffic_stops_geo", "contact_lifecycle_progression_count",
    ]
    weak = out[2]
    assert weak["match"] == {"cosine": 0.22, "lexical": False, "strength": "weak"}
    assert weak["score"] == 1.0   # the rank-normalized score is still reported, honestly labelled


def test_strong_business_hits_keep_the_top_tier(offline_grounding):
    hits = [
        _table("sales", "orders", 1.0, 1),
        _business("sales_order_value_system", 1.0, 100, kind="business_value_system"),
        _business("customer_order_count", 0.8, 101),
    ]
    relevance = {100: {"cosine": 0.34, "lexical": False}, 101: {"cosine": 0.21, "lexical": True}}
    out = server._blend_search_hits(hits, relevance, limit=3)
    assert [m["object"] for m in out] == [
        "sales_order_value_system", "customer_order_count", "sales.orders",
    ]
    assert out[0]["match"]["strength"] == "strong"          # cosine above the floor
    assert out[1]["match"] == {"cosine": 0.21, "lexical": True, "strength": "strong"}  # lexical rescue


def test_unknown_relevance_keeps_legacy_ordering_without_match_block(offline_grounding):
    hits = [_table("sales", "orders", 1.0, 1), _business("customer_order_count", 0.5, 101)]
    out = server._blend_search_hits(hits, None, limit=2)
    assert [m["object"] for m in out] == ["customer_order_count", "sales.orders"]
    assert "match" not in out[0]


def test_business_context_match_floor_is_env_tunable(monkeypatch):
    monkeypatch.setattr(server, "_BUSINESS_CONTEXT_MATCH_FLOOR", 0.5)
    assert server._business_context_match({7: {"cosine": 0.4, "lexical": False}}, 7)["strong"] is False
    monkeypatch.setattr(server, "_BUSINESS_CONTEXT_MATCH_FLOOR", 0.3)
    assert server._business_context_match({7: {"cosine": 0.4, "lexical": False}}, 7)["strong"] is True
    # A node the rankers never returned at all is weak, not unknown.
    assert server._business_context_match({7: {"cosine": 0.4, "lexical": False}}, 8)["strong"] is False


def test_search_data_leads_with_the_table_and_grounds_a_conjured_one(monkeypatch):
    """Callie, twice: "video games catalog" led with a 500-row label view keyed by title and the
    master was a later column. The fold find got applies here too; a table the index left out
    is conjured at the first part's position and grounded like any other table hit."""
    rows = [
        {"node_id": 1, "kind": "db_column", "schema_name": "store", "rel_name": "meta_video_games", "col_name": "main_category", "score": 0.9, "boosted_score": 0.9, "doc": "", "usage_touches": None},
        {"node_id": 2, "kind": "db_table", "schema_name": "meaning_labels", "rel_name": "meta_video_games__product_offering_role", "col_name": None, "score": 0.89, "boosted_score": 0.89, "doc": "", "usage_touches": None},
        {"node_id": 3, "kind": "db_column", "schema_name": "store", "rel_name": "meta_video_games", "col_name": "title", "score": 0.88, "boosted_score": 0.88, "doc": "", "usage_touches": 4},
        {"node_id": 4, "kind": "db_table", "schema_name": "store", "rel_name": "video_games", "col_name": None, "score": 0.8, "boosted_score": 0.8, "doc": "", "usage_touches": None},
    ]

    class _Cur:
        def execute(self, sql, params=None):
            return self
        def fetchall(self):
            return rows

    class _Conn:
        def __enter__(self):
            return _Cur()
        def __exit__(self, *exc):
            return False

    class _RoCur:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def execute(self, *a, **k):
            return self
        def fetchall(self):
            return []

    class _Ro:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
        def cursor(self):
            return _RoCur()

    grounded = []
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn())
    monkeypatch.setattr(server, "_ro", lambda *a, **k: _Ro())
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    def fake_samples(cur, s, r, n):
        assert hasattr(cur, "fetchall")                                              # the helpers take a CURSOR, not a connection
        grounded.append(f"{s}.{r}")
        return [{"row": 1}]
    monkeypatch.setattr(server, "_samples", fake_samples)
    monkeypatch.setattr(server, "_col_stats", lambda c, s, r, **k: {"title": {"ndv": 9}})
    monkeypatch.setattr(server, "_freshness", lambda c, s, r: {"rows": 137233})
    monkeypatch.setattr(server, "_find_label_bases", lambda objects: {
        "meaning_labels.meta_video_games__product_offering_role": {"base": "store.meta_video_games", "keys": ["title"], "sample_n": 500}})
    out = server.tool_search_data("video games catalog", limit=4)
    objects = [m["object"] for m in out["matches"]]
    assert objects == ["store.meta_video_games", "store.meta_video_games.main_category", "store.meta_video_games.title", "store.video_games"]
    top = out["matches"][0]
    assert top["folded"] is True and [v["object"] for v in top["via"]] == [
        "store.meta_video_games.main_category", "meaning_labels.meta_video_games__product_offering_role", "store.meta_video_games.title"]
    assert top["via"][1]["keyed_by"] == ["title"] and top["via"][1]["sample_rows"] == 500   # the label view is here, not beside the table
    assert top["samples"] == [{"row": 1}] and top["column_stats"] == {"title": {"ndv": 9}} and top["freshness"] == {"rows": 137233}
    assert grounded[-1] == "store.meta_video_games"                                  # the conjured table was grounded last


def test_search_hit_grounding_is_a_taste_not_the_row():
    """Callie: the clipped strings are the taste ("that is the garbage teaching me the column");
    the same paragraph three times, ten image objects and a video URL are the dump."""
    long = "Vivid, " * 100 + "ends here."
    image = {"hi_res": "https://m.media-amazon.com/images/I/71abcdef.jpg", "large": "https://m.media-amazon.com/images/I/71abcdef._SL.jpg", "variant": "MAIN"}
    row = {"title": "Ergonomic keyboard", "price": None, "rating_number": 223,
           "description": [long], "features": ["Wireless. Works with Mac.", "USB-C charging", "Backlit", "Quiet keys", "Spill resistant"],
           "categories": ["Video Games", "Accessories", "Keyboards", "Gaming"],
           "images": [image] * 10, "videos": [{"url": "https://www.amazon.com/vdp/0123abc?ref=dp_vse", "title": "Unboxing"}],
           "details": {f"Attribute {i}": f"value {i}" for i in range(12)}, "store": "Logitech"}
    rows = server._clip_sample_rows([row, dict(row, title="Second keyboard"), dict(row, title="Third", description=["short"])])
    r = rows[0]
    assert r["title"] == "Ergonomic keyboard" and r["price"] is None and r["rating_number"] == 223 and r["store"] == "Logitech"
    assert r["description"] == [rows[0]["description"][0]] and r["description"][0].endswith(" …")        # the paragraph, once, cut at a sentence
    assert r["features"] == ["Wireless. Works with Mac.", "USB-C charging", "Backlit", "… +2 more"]     # three scalars and a count
    assert r["categories"] == ["Video Games", "Accessories", "Keyboards", "… +1 more"]
    assert r["images"] == [{"hi_res": "https://m.media-amazon.com/…/71abcdef.jpg", "large": "https://m.media-amazon.com/…/71abcdef._SL.jpg", "variant": "MAIN"},
                           "… +9 more"]                                                               # ONE image present, its links as shapes
    assert r["videos"] == [{"url": "https://www.amazon.com/…/0123abc", "title": "Unboxing"}]
    assert r["details"] == {"keys": [f"Attribute {i}" for i in range(8)] + ["… +4 more"], "n_keys": 12}
    # a modest flat object keeps its values — the sales-order cube's dimension objects have five or six keys
    dim = {"region": "West", "channel": "web", "segment": "SMB", "tier": "gold", "country": "US", "fiscal_year": 2026}
    assert server._clip_sample_rows([{"dims": dim}])[0]["dims"] == dim
    assert server._clip_sample_rows([{"dims": {f"k{i}": i for i in range(9)}}])[0]["dims"]["n_keys"] == 9
    # …but modest is about the values: seven keys with a pricing paragraph in one of them is boilerplate, not a dimension
    details = {"Rated": "Everyone", "Item Weight": "1.2 pounds", "Manufacturer": "Sony", "Type": "Game", "Rank": "#1,204", "Date": "2016",
               "Pricing": "The strikethrough price is the List Price. Savings represents a discount off the List Price."}
    assert server._clip_sample_rows([{"details": details}])[0]["details"] == {"keys": list(details), "n_keys": 7}
    # …and a link is a shape already: an image object whose thumbnail URL shortens to 58 characters still keeps its values
    image = {"large": "https://m.media-amazon.com/images/I/51pqAznTA9L.jpg", "thumb": "https://m.media-amazon.com/images/I/51pqAznTA9L._SX38_SY50_CR,0,0,38,50_.jpg",
             "hi_res": "https://m.media-amazon.com/images/I/71hD-k6kaxL._SL1101_.jpg", "variant": "MAIN"}
    shown = server._clip_sample_rows([{"images": [image, image]}])[0]["images"]
    assert shown[0]["variant"] == "MAIN" and shown[0]["thumb"].startswith("https://m.media-amazon.com/…/") and shown[1] == "… +1 more"
    assert rows[1]["description"] == "= row 1" and rows[1]["images"] == "= row 1" and rows[1]["title"] == "Second keyboard"
    assert rows[2]["description"] == ["short"]                                                        # a different value stands on its own
    # column stats: URL fragments become shapes, and a list of links shows one
    stats = {"images": {"ndv": 9000, "top": ['[{"hi_res": "https://m.media-amazon.com/images/I/81x.jpg", "large": "https://m.media-amazon.com/images/I/81x._SL.jpg"}]',
                                             '[{"hi_res": "https://m.media-amazon.com/images/I/91y.jpg"}]']},
             "store": {"ndv": 40, "top": ["Nintendo", "Sony", "Microsoft", "Logitech"]}}
    out = server._clip_payload(stats)
    assert out["store"] == {"ndv": 40, "top": ["Nintendo", "Sony", "Microsoft", "… +1 more"]}
    assert out["images"]["top"] == ['[{"hi_res": "https://m.media-amazon.com/…/81x.jpg", "large": "https://m.media-amazon.com/…/81x._SL.jpg"}]', "… +1 more"]
    assert server._clip_payload("https://www.amazon.com/vdp/0123abc?ref=dp_vse") == "https://www.amazon.com/…/0123abc"
    assert server._clip_payload("short") == "short" and server._clip_payload(None) is None and server._clip_payload(3) == 3
    assert server._clip_payload("First sentence here. " + "x" * 300) == "First sentence here. …"


def test_a_wide_sample_row_keeps_every_column():
    """crime_feeds.detroit_incidents and public.bigfoot_sightings_locations on the test box
    have more than 24 columns; the first cut of the clipper dropped the 25th onward."""
    import datetime, decimal
    row = {f"c{i}": i for i in range(40)}
    row.update({"lat": decimal.Decimal("42.4359"), "seen": datetime.date(2023, 7, 31), "zip": 1002, "url": "http://www.bfro.net/GDB/show_report.asp?id=65996"})
    out = server._clip_sample_rows([row])[0]
    assert set(out) == set(row) and out["c39"] == 39 and out["lat"] == decimal.Decimal("42.4359") and out["seen"] == datetime.date(2023, 7, 31) and out["zip"] == 1002
    assert out["url"] == "http://www.bfro.net/…/show_report.asp"
    stats = {f"col{i}": {"ndv": i, "top": ["a", "b"]} for i in range(30)}
    assert set(server._clip_payload(stats)) == set(stats)                                   # a stats map keeps every column too
