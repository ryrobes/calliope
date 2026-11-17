"""The Briarpatch must fit the sketch layer's element cap on large installs.

The pilot feeds 160 usable domain proposals across 21 schemas; a card is a
shape plus a bound label plus an arrow, so drawing everything blew past the
500-element cap and the projector failed every tick. The budget keeps every
schema slice, draws each schema's top domains by priority, and collapses the
rest into one "+N more" label.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import briarpatch  # noqa: E402
import calliope  # noqa: E402


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Conn:
    """Answers the projector's queries from canned data, keyed by SQL text."""

    def __init__(self, domains, agents=(), wiki=(), wilds=(), events=()):
        self.domains, self.agents, self.wiki, self.wilds, self.events = (
            domains, list(agents), list(wiki), list(wilds), list(events)
        )

    def execute(self, sql, params=None):
        text = " ".join(str(sql).split())
        if "generated_specialist_avatars" in text:
            return _Rows([{"r": False}])
        if "to_regclass('rvbbit.wiki_pages')" in text:
            return _Rows([{"r": True}])
        if "FROM rvbbit.business_value_graph_summary summary" in text:
            return _Rows(self.domains)
        if "FROM rvbbit.calliope_specialists" in text:
            return _Rows(self.agents)
        if "FROM rvbbit.wiki_pages" in text:
            return _Rows(self.wiki)
        if "needs_revision" in text:
            return _Rows(self.wilds)
        if "knowledge_loop_events" in text:
            return _Rows(self.events)
        raise AssertionError(f"unexpected query: {text[:80]}")


def _domains(schemas: int, per_schema: int, needs_decision: set[str] = frozenset()):
    rows = []
    for s in range(schemas):
        schema = f"schema_{s:02d}"
        for d in range(per_schema):
            key = f"{schema}_domain_{d:02d}"
            rows.append({
                "domain_key": key,
                "name": f"Domain {s}.{d}",
                "status": "proposed",
                "adoption_state": "manual",
                "family_review_state": "needs_decision" if key in needs_decision else "provisional",
                "validated_metric_count": (d * 7) % 5,
                "schema": schema,
                "assertions": (d * 3) % 4,
            })
    rows.sort(key=lambda r: r["domain_key"])
    return rows


def _materialize(desired):
    """Run the desired scene through the real sketch layer and count elements."""
    ops = briarpatch._diff_operations([], desired)
    elements, revision = [], 1
    for start in range(0, len(ops), briarpatch.MAX_OPS_PER_APPLY):
        revision += 1
        elements, _summary = calliope._apply_sketch_operations(
            elements, ops[start:start + briarpatch.MAX_OPS_PER_APPLY], revision
        )
    return elements


def test_pilot_sized_map_fits_the_cap_through_the_real_sketch_layer():
    decision = {"schema_03_domain_07", "schema_10_domain_06"}
    conn = _Conn(
        _domains(21, 8, decision),
        agents=[{"specialist_key": "schema-00-domain-01", "display_name": "Clio", "busy": False}],
        wiki=[{"slug": f"schema-00-domain-01-note-{i}", "title": f"Note {i}", "flagged": False,
               "subject_key": None, "tags": []} for i in range(3)],
        wilds=[{"domain_key": f"wild_{i}", "status": "demoted"} for i in range(14)],
        events=[{"kind": "adopt", "n": 3}],
    )
    desired, _files = briarpatch._desired_model(conn)
    assert briarpatch.estimate_elements(desired) <= briarpatch.ELEMENT_BUDGET
    elements = _materialize(desired)
    assert 0 < len(elements) <= calliope._MAX_SKETCH_ELEMENTS
    # every schema keeps its slice, and crowded schemas say what they hid
    assert all(f"bp-src-schema_{s:02d}" in desired for s in range(21))
    more = [k for k in desired if k.startswith("bp-more-")]
    assert more, "168 domains cannot all be drawn; overflow labels expected"
    # families waiting on a decision are drawn ahead of plain proposals
    for key in decision:
        assert f"bp-dom-{key}" in desired


def test_small_install_draws_everything_with_no_overflow():
    conn = _Conn(_domains(3, 4))
    desired, _files = briarpatch._desired_model(conn)
    assert sum(1 for k in desired if k.startswith("bp-dom-")) == 12
    assert not [k for k in desired if k.startswith("bp-more-")]


def test_budgeted_scene_is_deterministic():
    conn = _Conn(_domains(21, 8))
    a, _ = briarpatch._desired_model(conn)
    b, _ = briarpatch._desired_model(conn)
    assert list(a) == list(b)
    assert a == b


def test_fit_domains_keeps_promoted_over_proposed():
    rows = _domains(2, 9)
    rows[0] = {**rows[0], "status": "promoted", "adoption_state": "affirmed", "validated_metric_count": 0}
    kept, overflow = briarpatch.fit_domains(rows, agent_domains=set(), wiki_pages=0, budget=80)
    kept_keys = {d["domain_key"] for d in kept}
    assert rows[0]["domain_key"] in kept_keys
    assert sum(overflow.values()) == len(rows) - len(kept)
