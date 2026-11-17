"""The capability index's forgetting must match its crawl, or search re-crawls forever."""
import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "crates" / "pg_rvbbit" / "sql" / "migrations"


def _latest_definition(function):
    pattern = re.compile(rf"CREATE OR REPLACE FUNCTION rvbbit\.{function}\(", re.I)
    defining = sorted(p for p in MIGRATIONS.glob("*.sql") if pattern.search(p.read_text(encoding="utf-8")))
    assert defining, function
    text = defining[-1].read_text(encoding="utf-8")
    start = pattern.search(text).start()
    return defining[-1].name, text[start:text.index("END $fn$;", start)]


def test_prune_keeps_docs_that_a_seed_row_describes():
    """capability_crawl() writes a doc for every capability_seed row, including SQL
    helpers filed as cap_operator that have no rvbbit.operators row. If the prune
    deletes those, capability_search_stale() never turns false and every
    capability_search runs a full crawl inside the agent's turn."""
    name, body = _latest_definition("capability_prune_stale")
    assert re.search(r"NOT EXISTS\s*\(\s*SELECT 1 FROM rvbbit\.capability_seed s WHERE s\.kind = c\.kind AND s\.name = c\.rel_name\)",
                     body), f"{name}: capability_prune_stale() must keep seed-described docs"
