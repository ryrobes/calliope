"""find — one search door into the data, with the receipts attached.

Source-level contract checks (no database): the MCP tool is registered on the
Warehouse lane and pinned for the coordinator, the tool result keeps the
envelope sections and hides internal schemas, migration 0437 is registered and
defines every seam + snapshot the tool relies on, and the crawl tail refreshes
the find snapshots.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import server  # noqa: E402

ROOT = HERE.parent.parent
SERVER_SRC = (HERE / "server.py").read_text(encoding="utf-8")
RECONCILER_SRC = (ROOT / "docker" / "calliope" / "reconcile-hermes-config.py").read_text(encoding="utf-8")
HERMES_CONFIG = (ROOT / "docker" / "calliope" / "hermes-config.yaml").read_text(encoding="utf-8")
MIGRATION = (ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" / "0437_find_primitive.sql").read_text(encoding="utf-8")
REGISTRY = (ROOT / "crates" / "pg_rvbbit" / "src" / "migrations.rs").read_text(encoding="utf-8")
SPECIALISTS_RS = (ROOT / "crates" / "pg_rvbbit" / "src" / "specialists" / "mod.rs").read_text(encoding="utf-8")


def test_find_is_registered_pinned_and_logged():
    assert 'mcp.tool(name="find")(_mcp_find)' in SERVER_SRC
    assert '"mcp__Warehouse__find",' in RECONCILER_SRC
    assert "- mcp__Warehouse__find" in HERMES_CONFIG
    # activity log: objects + summary carry the hits like search_data does
    assert 'if tool in {"search_data", "find"}:' in SERVER_SRC
    assert 'if tool == "find":' in SERVER_SRC


def test_pin_history_moves_boxes_forward_without_the_find_pin():
    # A box seeded with the previous default (everything but find) must be
    # recognised as "ours" so the reconciler adds find; an operator's own list
    # must not be.
    sys.path.insert(0, str(ROOT / "docker" / "calliope"))
    import importlib
    reconcile = importlib.import_module("reconcile-hermes-config")
    default = tuple(reconcile.PINNED_TOOLS_DEFAULT)
    assert "mcp__Warehouse__find" in default
    # find was the newest pin when this test was written; the teach verbs, the bridge-tax
    # thirteen and meaning_tree came after it, so the pre-find list is the one BEFORE those.
    before_find = tuple(n for n in reconcile._PINNED_TOOLS_BEFORE_TEACH_VERBS if n != "mcp__Warehouse__find")
    assert "mcp__Warehouse__find" in reconcile._PINNED_TOOLS_BEFORE_TEACH_VERBS
    assert before_find in reconcile.PINNED_TOOLS_PREVIOUS_DEFAULTS
    before_look = tuple(n for n in before_find if n != "mcp__Warehouse__look_at_desktop")
    assert before_look in reconcile.PINNED_TOOLS_PREVIOUS_DEFAULTS
    # and every recorded previous default is a strict subset of today's, so a box on any of them moves forward
    for prev in reconcile.PINNED_TOOLS_PREVIOUS_DEFAULTS:
        assert set(prev) < set(default), prev


def test_tool_find_hides_internal_schemas_and_keeps_the_envelope(monkeypatch):
    payload = {
        "query": "overdue balance",
        "elapsed_ms": 42,
        "degraded": [],
        "gates": {"rerank": {"applied": True}},
        "seams": {"on": ["dense", "lexical", "values"]},
        "hits": [
            {"rank": 1, "node_id": 1, "kind": "db_table", "schema": "rvbbit", "rel": "receipts", "col": None,
             "score": 1.0, "seams": {"lexical": {"rank": 1}}},
            {"rank": 2, "node_id": 2, "kind": "db_column", "schema": "salesforce", "rel": "lead", "col": "company",
             "score": 0.9, "seams": {"values": {"rank": 1}},
             "matched_values": [{"value": "Cleveland Family Dental Center", "n": 3}],
             "lineage": {"adapter": "fivetran:salesforce", "role": "business"},
             "fresh": {"live_rows": 225279}, "usage": {"touches_30d": 12},
             "meaning": {"domains": [{"key": "leads"}]},
             "ask": {"sample_sql": "SELECT company, count(*) AS n FROM salesforce.lead GROUP BY 1"},
             "object": {"data_type": "character varying(255)", "ndv": 5000}},
            {"rank": 3, "node_id": 3, "kind": "cube", "schema": "cubes", "rel": "student_finance_risk_cube", "col": None,
             "score": 0.5, "seams": {"dense": {"rank": 2}}},
        ],
    }
    captured = {}

    class _Cur:
        def execute(self, sql, params):
            captured["sql"] = sql
            captured["params"] = params
            return self

        def fetchone(self):
            return {"j": payload}

    class _Conn:
        def __enter__(self):
            return _Cur()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn())
    out = server.tool_find("overdue balance", limit=5, schema=None, kinds=["db_column", "cube", "bogus"], rerank=True, judge=False)
    assert "rvbbit.find_json(" in captured["sql"]
    assert captured["params"][1] == 10          # over-fetch: 2 × limit
    assert '"kinds": ["db_column", "cube"]' in captured["params"][2]
    assert '"rerank": true' in captured["params"][2]
    objects = [m["object"] for m in out["matches"]]
    assert objects == ["salesforce.lead.company", "cubes.student_finance_risk_cube"]   # rvbbit.* hidden
    hit = out["matches"][0]
    assert hit["matched_values"][0]["value"] == "Cleveland Family Dental Center"
    assert hit["lineage"]["adapter"] == "fivetran:salesforce"
    assert hit["fresh"]["live_rows"] == 225279
    assert hit["usage"]["touches_30d"] == 12
    assert hit["meaning"]["domains"][0]["key"] == "leads"
    assert hit["ask"]["sample_sql"].startswith("SELECT company")
    assert hit["about"]["data_type"].startswith("character varying")
    assert out["gates"]["rerank"]["applied"] is True
    assert out["degraded"] == []
    assert "note" not in out


def test_tool_find_reports_degraded_seams_when_empty(monkeypatch):
    payload = {"query": "x", "hits": [], "degraded": ["dense: catalog exceeds the brute-force cap"], "gates": {}}

    class _Cur:
        def execute(self, sql, params):
            return self

        def fetchone(self):
            return {"j": payload}

    class _Conn:
        def __enter__(self):
            return _Cur()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn())
    out = server.tool_find("x", limit=3)
    assert out["matches"] == []
    assert "degraded" in out["note"] and "brute-force" in out["note"]


def test_migration_0437_is_registered_and_defines_the_seams():
    assert '"0437_find_primitive"' in REGISTRY
    assert 'include_str!("../sql/migrations/0437_find_primitive.sql")' in REGISTRY
    for needle in (
        "ADD COLUMN IF NOT EXISTS lex tsvector",
        "CREATE INDEX IF NOT EXISTS catalog_docs_lex_gin",
        "CREATE OR REPLACE FUNCTION rvbbit.catalog_lexical_knn(",
        "CREATE OR REPLACE FUNCTION rvbbit.dense_seam_status(",
        "ALTER TABLE rvbbit.catalog_vec_new RENAME TO catalog_vec",
        "CREATE OR REPLACE FUNCTION rvbbit.pgvector_refresh_catalog_if_stale(",
        "CREATE TABLE IF NOT EXISTS rvbbit.relation_refs (",
        "CREATE TABLE IF NOT EXISTS rvbbit.usage_snapshot (",
        "CREATE TABLE IF NOT EXISTS rvbbit.relation_lineage (",
        "CREATE TABLE IF NOT EXISTS rvbbit.value_index (",
        "CREATE OR REPLACE PROCEDURE rvbbit.value_index_sweep(",
        "CREATE OR REPLACE FUNCTION rvbbit.value_knn(",
        "CREATE OR REPLACE FUNCTION rvbbit.find_envelope(",
        "CREATE OR REPLACE FUNCTION rvbbit.find_json(",
        "CREATE OR REPLACE FUNCTION rvbbit.find(",
        "CREATE OR REPLACE FUNCTION rvbbit.find_flat(",
        "CREATE OR REPLACE FUNCTION rvbbit.find_refresh(",
        "rvbbit.call_specialist_batch('rerank'",
        "rvbbit.clover_decide(v_q_state, v_questions",
    ):
        assert needle in MIGRATION, needle
    # loud dense cap + the crawl tail refresh
    assert "dense seam OFF for graph" in MIGRATION
    assert "PERFORM rvbbit.find_refresh(v_graph);" in MIGRATION
    # PII-shaped columns never enter the value index
    assert "value_index_skip_column_patterns" in MIGRATION
    assert "password|passwd" in MIGRATION


def test_find_is_discoverable_through_the_capability_graph():
    caps = (ROOT / "crates" / "pg_rvbbit" / "sql" / "migrations" / "0438_find_capabilities.sql").read_text(encoding="utf-8")
    assert '"0438_find_capabilities"' in REGISTRY
    # the MCP corpus row carries the agent-facing docstring, not an empty summary
    assert "('find', 'Find warehouse tables, columns, cubes, and literal values by meaning" in caps
    assert "('look_at_desktop', 'Look at the shared Desktop Lab desktop" in caps
    # the SQL syntax card + the crawl that publishes both
    assert "'find',\n    'cap_syntax'," in caps
    assert "PERFORM rvbbit.capability_crawl(true);" in caps
    # generator resolves _mcp_* wrapper docstrings (the text agents actually see)
    gen = (ROOT / "scripts" / "gen_capability_mcp_tools.py").read_text(encoding="utf-8")
    assert 'docs.get(f"_mcp_{name}", "")' in gen
    # refresh bookkeeping: throttled, logged, fired by every crawl path, probe for humans
    assert "CREATE OR REPLACE FUNCTION rvbbit.find_refresh(" in caps
    assert "DROP FUNCTION IF EXISTS rvbbit.find_refresh(text);" in caps
    assert "CREATE TRIGGER rvbbit_find_refresh_after_catalog" in caps
    assert "CREATE OR REPLACE FUNCTION rvbbit.find_status(" in caps


def test_fresh_installs_schedule_the_find_jobs():
    for rel in ("docker/init/03-seed-scheduled-tasks.sh", "docker/uber/bootstrap.sh"):
        seed = (ROOT / rel).read_text(encoding="utf-8")
        assert "cron.schedule_in_database('rvbbit_find_refresh'" in seed, rel
        assert "rvbbit.find_refresh(''db_catalog'', true, ''cron'')" in seed, rel
        assert "cron.schedule_in_database('rvbbit_value_index_sweep'" in seed, rel
        assert "CALL rvbbit.value_index_sweep(120);" in seed, rel
    # the tier staleness check counts docs at the tier's own dimension
    assert "array_length(embedding, 1) = v_idim" in MIGRATION


def test_batched_specialist_call_exists_for_the_rerank_gate():
    assert "fn call_specialist_batch(specialist_name: &str, inputs: pgrx::JsonB)" in SPECIALISTS_RS
    assert "predict_batch(&spec, &items)" in SPECIALISTS_RS


def test_tool_find_folds_parts_under_their_table(monkeypatch):
    """"video games catalog" on the test box led with five columns of store.meta_video_games and a
    500-row label view keyed by title; the master table was not in the top six. A table outranks
    its own parts; a literal-value hit is left alone."""
    hits = [
        {"kind": "db_column", "schema": "store", "rel": "meta_video_games", "col": "main_category", "score": 1.0, "seams": {"dense": {"rank": 2}}},
        {"kind": "db_table", "schema": "meaning_labels", "rel": "meta_video_games__product_offering_role", "col": None, "score": 0.9999, "seams": {"lexical": {"rank": 1}},
         "object": {"n_rows": 500, "comment": "Product offering role … Join on title to store.meta_video_games."}},
        {"kind": "db_column", "schema": "store", "rel": "meta_video_games", "col": "title", "score": 0.9998, "seams": {"dense": {"rank": 6}}},
        {"kind": "db_column", "schema": "salesforce", "rel": "lead", "col": "company", "score": 0.99, "seams": {"values": {"rank": 1}},
         "matched_values": [{"value": "Coast Dental", "n": 3}]},
        {"kind": "db_table", "schema": "store", "rel": "video_games", "col": None, "score": 0.9997, "seams": {"dense": {"rank": 9}}},
        {"kind": "db_table", "schema": "store", "rel": "meta_video_games", "col": None, "score": 0.9, "seams": {"dense": {"rank": 12}}, "object": {"n_rows": 137233}},
    ]
    payload = {"query": "video games catalog", "hits": hits, "degraded": [], "gates": {}, "seams": {}}

    class _Cur:
        def execute(self, sql, params=None):
            return self
        def fetchone(self):
            return {"j": payload}

    class _Conn:
        def __enter__(self):
            return _Cur()
        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn())
    monkeypatch.setattr(server, "_find_label_bases", lambda objects: {
        "meaning_labels.meta_video_games__product_offering_role": {"base": "store.meta_video_games", "keys": ["title"], "sample_n": 500}})
    out = server.tool_find("video games catalog", limit=8)
    objects = [m["object"] for m in out["matches"]]
    assert objects[0] == "store.meta_video_games"                                  # the master leads
    table = out["matches"][0]
    assert [v["object"] for v in table["via"]] == ["store.meta_video_games.main_category",
                                                   "meaning_labels.meta_video_games__product_offering_role",
                                                   "store.meta_video_games.title"]
    assert table["via"][1] == {"object": "meaning_labels.meta_video_games__product_offering_role", "kind": "db_table",
                               "label": "product_offering_role", "keyed_by": ["title"], "sample_rows": 500}
    assert table["about"]["n_rows"] == 137233 and "folded" not in table              # the real hit moved up, not a stand-in
    assert objects.count("store.meta_video_games") == 1
    assert not any(o.startswith("meaning_labels.") for o in objects)                # the label view lives under via, not beside the table
    assert objects[1:3] == ["store.meta_video_games.main_category", "store.meta_video_games.title"]
    assert objects.index("salesforce.lead.company") < objects.index("store.video_games")   # the value hit kept its place, no table conjured for it
    assert "salesforce.lead" not in objects


def test_find_fold_conjures_the_table_when_the_index_left_it_out():
    matches = [
        {"object": "store.meta_video_games.title", "kind": "db_column", "score": 0.8},
        {"object": "store.meta_video_games.store", "kind": "db_column", "score": 0.7},
        {"object": "public.orders", "kind": "db_table", "score": 0.6},
        {"object": "public.orders.total", "kind": "db_column", "score": 0.5},
    ]
    out = server._find_fold_under_tables(matches, {})
    assert [m["object"] for m in out] == ["store.meta_video_games", "store.meta_video_games.title", "store.meta_video_games.store",
                                          "public.orders", "public.orders.total"]
    # a column OF a label view folds under the view's base table, not under the view
    bases = {"meaning_labels.meta_video_games__product_offering_role": {"base": "store.meta_video_games", "keys": ["title"], "sample_n": 500}}
    out2 = server._find_fold_under_tables([{"object": "meaning_labels.meta_video_games__product_offering_role.title", "kind": "db_column", "score": 0.9},
                                           {"object": "store.meta_video_games", "kind": "db_table", "score": 0.5}], bases)
    assert [m["object"] for m in out2] == ["store.meta_video_games"]                    # the view's column folded away too
    assert out2[0]["via"] == [{"object": "meaning_labels.meta_video_games__product_offering_role.title", "kind": "db_column",
                               "label": "product_offering_role.title", "keyed_by": ["title"], "sample_rows": 500}]
    assert "folded" not in out2[0]                                                      # the real table moved up
    assert out[0]["folded"] is True and out[0]["score"] == 0.8 and [v["object"] for v in out[0]["via"]] == ["store.meta_video_games.title", "store.meta_video_games.store"]
    assert "via" not in out[3]                                                          # orders already led its column: untouched
    assert server._find_fold_under_tables([{"object": "a.b", "kind": "db_table"}], {}) == [{"object": "a.b", "kind": "db_table"}]

