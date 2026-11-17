"""0506: the artifact semantic compiler names the measure, declares every slice, reads the chat."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "crates/pg_rvbbit/sql/migrations/0506_semantic_compiler_measures_and_slices.sql").read_text()
SERVER = (ROOT / "services/calliope/server.py").read_text()


def test_the_prompt_separates_measure_from_slice_and_keeps_exact_replay():
    assert "UPDATE rvbbit.operators" in SQL and "WHERE name = 'artifact_semantic_enrich'" in SQL
    for phrase in ('"measure"', '"conditions"', '"asked_as"', '"dimension": "time|cohort|place|segment|entity|other"',
                   '"fixed": true', "business_context.known_measures", "reuse that exact name",
                   "must return exactly the rendered value", "UNTRUSTED DATA"):
        assert phrase in SQL, phrase
    # The compiler never follows the conversation it is shown.
    assert "the conversation in business_context" in SQL.split("MEASURE AND SLICE")[0]


def test_the_server_sends_context_and_requeues_under_a_new_prompt_version():
    assert '_SEMANTIC_ENRICH_PROMPT_VERSION = "artifact-semantic-enricher.v5"' in SERVER
    assert '"business_context": _semantic_business_context(job, queries)' in SERVER
    assert '"0506_semantic_compiler_measures_and_slices"' in (ROOT / "crates/pg_rvbbit/src/migrations.rs").read_text()


GRIDS = (ROOT / "crates/pg_rvbbit/sql/migrations/0507_semantic_compiler_values_and_grids.sql").read_text()


def test_0507_keeps_callouts_as_values_and_describes_grids_once():
    """A chart or table is one grid, never one value per cell; the publisher enforces both halves."""
    assert "UPDATE rvbbit.operators" in GRIDS and "WHERE name = 'artifact_semantic_enrich'" in GRIDS
    for phrase in ('"grids": [', '"shape": "table"', '"anchor": {"family": "family_2"}', 'role is "callout"', "grid_member",
                   "artifact_packet.dom.families", '"role": "dimension"', "differ only in what a slice is set to",
                   "MEASURE AND SLICE", "reuse that exact name", "UNTRUSTED DATA"):
        assert phrase in GRIDS, phrase
    assert "one parameterized semantic template" not in GRIDS, "a repeated run is a grid now, not a per-cell template"
    for name in ("_semantic_annotate_candidates(dom)", "_verify_semantic_grid(grid, evidence.get(\"dom\"))", "_semantic_value_series(verified)",
                 'semantic_map["grids"] = grids'):
        assert name in SERVER, name
    assert '"0507_semantic_compiler_values_and_grids"' in (ROOT / "crates/pg_rvbbit/src/migrations.rs").read_text()


SLICES = (ROOT / "crates/pg_rvbbit/sql/migrations/0508_semantic_compiler_slice_names.sql").read_text()


def test_0508_names_slices_the_way_other_artifacts_do():
    """Two values compare over the same slice only when both call it the same thing."""
    assert "UPDATE rvbbit.operators" in SLICES and "WHERE name = 'artifact_semantic_enrich'" in SLICES
    for phrase in ("business_context.known_slices", "reuse that exact name and type", "is start_date and end_date",
                   "its length is window_days", "never turn one kind into the other", "the value must keep moving",
                   'keeps that control as its "source"', "as_of_date", "time_zone", '"slice: current week"',
                   "VALUES AND GRIDS", "grid_member", "MEASURE AND SLICE", "reuse that exact name", "UNTRUSTED DATA"):
        assert phrase in SLICES, phrase
    assert "known_slices=slices" in SERVER and "def _semantic_known_slices(conn, dashboard_id):" in SERVER
    assert '"0508_semantic_compiler_slice_names"' in (ROOT / "crates/pg_rvbbit/src/migrations.rs").read_text()
