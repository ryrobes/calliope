"""Suggestions chosen in the composer rail: how they are decoded, how they join
a turn's object refs, and how they land on the Stage as ordinary surfaces."""

import pytest

import calliope


def test_pins_decode_to_metric_handles():
    decoded = calliope._decode_composer_pins([
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "metric", "ref_id": "active_seats"},
    ])
    assert decoded == [
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "metric", "ref_id": "active_seats"},
    ]


def test_pins_drop_duplicates_unstageable_kinds_and_control_characters():
    decoded = calliope._decode_composer_pins([
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "artifact", "ref_id": "revenue-board"},
        # A conversation is somewhere you go, not a Stage object.
        {"kind": "conversation", "ref_id": "some-thread"},
        {"kind": "document", "ref_id": "some-doc"},
        {"kind": "metric", "ref_id": ""},
        {"kind": "metric", "ref_id": "active\x00_seats\n"},
    ])
    assert decoded == [
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "artifact", "ref_id": "revenue-board"},
        {"kind": "metric", "ref_id": "active_seats"},
    ]


def test_pins_are_capped_and_reject_a_non_list():
    decoded = calliope._decode_composer_pins(
        [{"kind": "metric", "ref_id": f"m{i}"} for i in range(20)]
    )
    assert len(decoded) == 8
    assert calliope._decode_composer_pins(None) == []
    with pytest.raises(ValueError):
        calliope._decode_composer_pins({"kind": "metric", "ref_id": "gross_revenue"})


def test_pins_join_object_refs_without_displacing_inline_markers():
    inline = [{"kind": "metric", "ref_id": "churn", "label": "Churn"}]
    merged = calliope._merge_composer_pin_refs(
        inline, [{"kind": "metric", "ref_id": "gross_revenue"}]
    )
    assert merged[0] == inline[0]
    assert merged[1] == {"kind": "metric", "ref_id": "gross_revenue", "pinned": True}


class _Recorder:
    def __init__(self):
        self.projected = None

    def insert(self, conn_factory, session_id, turn_id, projected):
        self.projected = projected
        return [{"id": f"surface-{i}"} for i, _ in enumerate(projected)]


def _detail(name, params, owner=None, days=None, bucket=None):
    if name == "missing_metric":
        raise RuntimeError("no such metric")
    return {
        "title": "Gross revenue",
        "canonical_key": "finance/gross_revenue",
        "version": 4,
        "display": {"currency": "USD"},
        "description": "Money booked",
        "grain": "day",
        "category": "Finance",
        "trend": {"direction": "up", "percent": 3.2},
        "series": [
            {"value": 10, "observed_at": "2026-09-19T00:00:00Z"},
            {"value": 12, "observed_at": "2026-09-20T00:00:00Z", "observation_id": "obs-2"},
        ],
    }


def test_a_pinned_suggestion_lands_as_an_ordinary_metric_surface(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    inserted = calliope._materialize_composer_pins(
        object(), _detail, "session-1", "turn-1",
        [{"kind": "metric", "ref_id": "gross_revenue"}], "viewer@example.com",
    )
    assert len(inserted) == 1
    (surface,) = recorder.projected
    assert surface["kind"] == "metric"
    assert surface["title"] == "Gross revenue"
    assert surface["tool_name"] == "composer_pin"
    assert surface["tool_call_id"] == "composer-pin:turn-1:metric:gross_revenue"
    assert surface["payload"]["name"] == "gross_revenue"
    assert surface["payload"]["result"] == 12
    # Tool history is newest-first; the browser normalizes before drawing.
    assert surface["payload"]["observations"][0]["value"] == 12
    assert surface["payload"]["trend"] == {"direction": "up", "percent": 3.2}
    assert surface["source"]["origin"] == "composer_pin"
    assert surface["source"]["observation_id"] == "obs-2"


def test_an_unresolvable_pin_is_skipped_rather_than_failing_the_turn(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    inserted = calliope._materialize_composer_pins(
        object(), _detail, "session-1", "turn-1",
        [{"kind": "metric", "ref_id": "missing_metric"},
         {"kind": "metric", "ref_id": "gross_revenue"}],
        "viewer@example.com",
    )
    assert len(inserted) == 1
    assert [s["payload"]["name"] for s in recorder.projected] == ["gross_revenue"]


def test_no_resolvable_pins_writes_nothing(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    assert calliope._materialize_composer_pins(
        object(), _detail, "session-1", "turn-1",
        [{"kind": "metric", "ref_id": "missing_metric"}], "viewer@example.com",
    ) == []
    assert recorder.projected is None


class _ArtifactConn:
    """Stands in for the dashboards/artifact_index join, recording the owner
    the visibility predicate was given."""

    def __init__(self, row, seen):
        self.row = row
        self.seen = seen

    def execute(self, sql, params):
        self.seen.append((sql, params))
        slug, owner = params
        row = self.row if (self.row and self.row["ref"] == slug and owner == "viewer@example.com") else None
        return type("C", (), {"fetchone": lambda _self, r=row: r})()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _artifact_factory(row, seen):
    return lambda: _ArtifactConn(row, seen)


_ARTIFACT_ROW = {
    "ref": "revenue-board",
    "kind": "dashboard",
    "version": 4,
    "title": "Revenue board",
    "description": "Revenue by region and segment",
    "owner": "author@example.com",
    "status": "published",
    "path": "/calliope/artifacts/revenue-board",
    "lineage": {"runtime_kind": "vega"},
}


def test_a_pinned_artifact_lands_as_an_ordinary_artifact_surface(monkeypatch):
    recorder, seen = _Recorder(), []
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    inserted = calliope._materialize_composer_pins(
        _artifact_factory(_ARTIFACT_ROW, seen), _detail, "session-1", "turn-1",
        [{"kind": "artifact", "ref_id": "revenue-board"}], "viewer@example.com",
    )
    assert len(inserted) == 1
    (surface,) = recorder.projected
    assert surface["kind"] == "artifact"
    assert surface["title"] == "Revenue board"
    assert surface["tool_name"] == "composer_pin"
    assert surface["artifact_slug"] == "revenue-board"
    # The Stage keeps the exact version chosen, not the mutable gallery alias.
    assert surface["artifact_version"] == 4
    assert surface["payload"]["display_url"].endswith("/versions/4")
    assert surface["lineage_key"] == "artifact:revenue-board"
    assert surface["source"]["origin"] == "composer_pin"


def test_an_artifact_pin_is_gated_by_the_same_visibility_check_as_the_gallery(monkeypatch):
    recorder, seen = _Recorder(), []
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    inserted = calliope._materialize_composer_pins(
        _artifact_factory(_ARTIFACT_ROW, seen), _detail, "session-1", "turn-1",
        [{"kind": "artifact", "ref_id": "revenue-board"}], "stranger@example.com",
    )
    assert inserted == []
    assert recorder.projected is None
    sql, params = seen[0]
    assert "artifact_can_view" in sql
    assert params == ("revenue-board", "stranger@example.com")


def test_an_unpublished_artifact_is_skipped(monkeypatch):
    recorder, seen = _Recorder(), []
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    assert calliope._materialize_composer_pins(
        _artifact_factory({**_ARTIFACT_ROW, "version": None}, seen), _detail,
        "session-1", "turn-1",
        [{"kind": "artifact", "ref_id": "revenue-board"}], "viewer@example.com",
    ) == []


class _IndexConn:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params):
        assert "artifact_can_view" in sql
        assert "NOT d.archived" in sql
        return type("C", (), {"fetchall": lambda _s: self.rows})()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def test_the_artifact_index_uses_the_gallery_word_for_each_kind():
    calliope._COMPOSER_ARTIFACT_CACHE.clear()
    rows = [
        {"slug": "revenue-board", "name": "Revenue board", "description": "By region",
         "latest_version": 4, "app_kind": "dashboard", "updated_at": None},
        {"slug": "q3-review", "name": "Q3 review", "description": "",
         "latest_version": 2, "app_kind": "presentation", "updated_at": None},
        {"slug": "the-long-read", "name": "The long read", "description": "",
         "latest_version": 31, "app_kind": "editorial", "updated_at": None},
        {"slug": "mystery", "name": None, "description": None,
         "latest_version": 1, "app_kind": None, "updated_at": None},
    ]
    index = calliope._composer_artifact_index(lambda: _IndexConn(rows), "viewer@example.com")
    assert [(a["name"], a["category"]) for a in index] == [
        ("revenue-board", "Dashboard"),
        ("q3-review", "Presentation"),
        ("the-long-read", "Editorial"),
        ("mystery", "Artifact"),
    ]
    # A missing title still reads as a name, not a slug.
    assert index[3]["title"] == "Mystery"
    calliope._COMPOSER_ARTIFACT_CACHE.clear()


def test_the_artifact_index_is_cached_per_viewer():
    calliope._COMPOSER_ARTIFACT_CACHE.clear()
    calls = []

    def factory(owner_rows):
        def make():
            calls.append(1)
            return _IndexConn(owner_rows)
        return make

    rows = [{"slug": "a", "name": "A", "description": "", "latest_version": 1,
             "app_kind": "dashboard", "updated_at": None}]
    calliope._composer_artifact_index(factory(rows), "one@example.com")
    calliope._composer_artifact_index(factory(rows), "one@example.com")
    assert len(calls) == 1
    # A different viewer must not be served the first one's visible set.
    calliope._composer_artifact_index(factory(rows), "two@example.com")
    assert len(calls) == 2
    calliope._COMPOSER_ARTIFACT_CACHE.clear()


def test_pins_resolve_side_by_side_and_keep_pick_order(monkeypatch):
    import threading
    import time

    recorder = _Recorder()
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    live, peak, lock = [0], [0], threading.Lock()

    def slow_detail(name, params, owner=None, days=None, bucket=None):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.15)
        with lock:
            live[0] -= 1
        return {**_detail("gross_revenue", None), "title": name.replace("_", " ").title()}

    pins = [{"kind": "metric", "ref_id": f"metric_{i}"} for i in range(6)]
    started = time.monotonic()
    inserted = calliope._materialize_composer_pins(
        object(), slow_detail, "session-1", "turn-1", pins, "viewer@example.com",
    )
    elapsed = time.monotonic() - started
    assert len(inserted) == 6
    # Six reads of 0.15 s would be 0.9 s in a row; side by side they overlap.
    assert peak[0] > 1, peak
    assert elapsed < 0.6, elapsed
    # Concurrency must not shuffle the Stage: the order is the order picked.
    assert [s["payload"]["name"] for s in recorder.projected] == [f"metric_{i}" for i in range(6)]


def test_a_failing_pin_beside_others_costs_only_itself(monkeypatch):
    recorder = _Recorder()
    monkeypatch.setattr(calliope, "_insert_surfaces", recorder.insert)
    pins = [
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "metric", "ref_id": "missing_metric"},
        {"kind": "metric", "ref_id": "gross_revenue"},
        {"kind": "artifact", "ref_id": "no-such-board"},
    ]
    inserted = calliope._materialize_composer_pins(
        _artifact_factory(None, []), _detail, "session-1", "turn-1", pins, "viewer@example.com",
    )
    assert len(inserted) == 2
    assert [s["kind"] for s in recorder.projected] == ["metric", "metric"]
