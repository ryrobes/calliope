"""Values from dashboards: the library's catalog, its search, and finding a
placed value again after its dashboard was recompiled."""
import pytest

import stage_arrangements as sa
import stage_values as sv


def obj(id_, label, measure=None, **params):
    return {"id": id_, "meaning": {k: v for k, v in {"label": label, "measure": measure, "description": f"{label} described."}.items() if v},
            "parameters": params, "evaluator": {"sql": "select 1 as value"}, "definition_hash": f"h-{id_}"}


TERM = {"type": "text", "default": "Fall 2026", "dimension": "cohort", "fixed": True, "label": "Term"}
ROWS = [
    {"slug": "paid-media", "name": "Paid Media Master Control", "owner_email": "will@example.invalid", "latest_version": 5,
     "version": 5, "updated_at": "2026-11-12T04:00:00+00:00", "href": "/d/paid-media", "authored": None,
     "generated": {"objects": [obj("enrolled", "Enrollment pace", "net enrolled students", term=TERM),
                               obj("spend", "Spend 7d", "paid media spend", term=TERM)]}},
    {"slug": "fall-console", "name": "Fall 2026 Decision Console", "owner_email": "ryan@example.invalid", "latest_version": 2,
     "version": 2, "updated_at": "2026-11-10T04:00:00+00:00", "href": "/d/fall-console",
     "authored": {"objects": [obj("enrolled", "Enrolled (authored)", "net enrolled students")]},
     "generated": {"objects": [obj("enrolled", "Enrolled", "net enrolled students"), obj("seats", "Open seats", "open seats"),
                               {"id": "broken", "meaning": {"label": "No SQL"}, "evaluator": {}}]}},
]


def test_catalog_lists_each_value_once_and_never_its_sql():
    items = sv.flatten(ROWS)
    assert [i["key"] for i in items] == ["paid-media:enrolled", "paid-media:spend", "fall-console:enrolled", "fall-console:seats"]
    assert items[2]["label"] == "Enrolled (authored)", "what the builder wrote wins over what the compiler found"
    first = items[0]
    assert first["artifact_name"] == "Paid Media Master Control" and first["owner"] == "will" and first["version"] == 5
    assert first["slices"] == [{"name": "term", "label": "Term", "type": "text", "default": "Fall 2026", "value": "Fall 2026",
                                "dimension": "cohort", "fixed": True}]
    assert "sql" not in str(items).lower() and "evaluator" not in str(items)


def test_the_library_lists_only_values_that_name_their_measure():
    """An older analysis has a label and SQL but no measure or declared slices:
    it stays out of the library until its dashboard is recompiled."""
    rows = [{**ROWS[0], "generated": {"objects": [obj("old", "Enrolled students"), obj("new", "Enrollment pace", "net enrolled students")]}}]
    assert [i["key"] for i in sv.flatten(rows)] == ["paid-media:new"]
    assert [i["key"] for i in sv.flatten(rows, named_only=False)] == ["paid-media:old", "paid-media:new"]
    # A block placed earlier is still found: reading never applies the gate.
    assert sv.match_object(rows[0]["generated"]["objects"], "old")[1] == "id"


def test_search_needs_every_word_and_prefers_the_label_and_measure():
    items = sv.flatten(ROWS)
    shown, total = sv.search(items, "enrolled students")
    assert total == 2 and {i["artifact"] for i in shown} == {"paid-media", "fall-console"}
    assert [i["key"] for i in sv.search(items, "paid spend")[0]] == ["paid-media:spend"]
    assert sv.search(items, "will")[1] == 2, "a person's name finds their dashboards' values"
    assert sv.search(items, "enrolled zebra") == ([], 0)
    newest_first, total = sv.search(items, "", limit=3)
    assert total == 4 and [i["artifact"] for i in newest_first] == ["paid-media", "paid-media", "fall-console"]


def test_a_placed_value_is_found_again_after_a_recompile_renames_it():
    objects = [obj("net_enrolled", "Enrollment pace", "net enrolled students"), obj("spend", "Spend 7d", "paid media spend"),
               obj("pace_b", "Enrollment pace", "pace of goal")]
    assert sv.match_object(objects, "spend")[1] == "id"
    found, how = sv.match_object(objects, "enrolled", "enrollment pace", "net enrolled students")
    assert (found["id"], how) == ("net_enrolled", "measure")
    assert sv.match_object(objects, "enrolled", "Enrollment pace") == (None, None), "two values share the label: do not guess"
    assert sv.match_object(objects, "old_spend", "spend 7d")[1] == "label"
    assert sv.match_object(objects, "gone") == (None, None)


def test_slices_show_what_a_block_changed():
    slices = sv.slices_of(obj("e", "E", term=TERM, weeks={"type": "integer", "default": 12}), {"term": "Spring 2027"})
    assert slices[0]["value"] == "Spring 2027" and slices[0]["changed"] is True and slices[0]["default"] == "Fall 2026"
    assert slices[1] == {"name": "weeks", "label": "Weeks", "type": "integer", "default": 12, "value": 12}


def test_slice_overrides_are_bounded_scalars():
    assert sv.params({"term": "Spring 2027", "weeks": 12, "paid": True, "states": ["FL", "GA"]}) == {
        "term": "Spring 2027", "weeks": 12, "paid": True, "states": ["FL", "GA"]}
    assert sv.params(None) == {} and sv.params({}) == {}
    for bad in ({"bad name": 1}, {"x": {"nested": 1}}, {"x": [{"a": 1}]}, ["term"], {f"p{i}": i for i in range(40)}):
        with pytest.raises(ValueError):
            sv.params(bad)
    for bad in ("../etc", "", "a b"):
        with pytest.raises(ValueError):
            sv.slug(bad)


def value_cell(**over):
    base = {"id": "v", "kind": "value", "artifact": "paid-media", "object": "enrolled", "from_version": 5,
            "source_label": "Enrollment pace", "measure": "net enrolled students", "label": "Enrolled",
            "params": {"term": "Spring 2027"}, "x": 0, "y": 0, "w": 3, "h": 2}
    base.update(over)
    return base


def test_an_arrangement_accepts_a_value_block_and_checks_who_placed_it(monkeypatch):
    import artifact_access

    clean = sa.validate({"title": "Mix", "width": 12, "cells": [value_cell()]})
    cell = clean["cells"][0]
    assert cell == {"id": "v", "kind": "value", "x": 0, "y": 0, "w": 3, "h": 2, "artifact": "paid-media", "object": "enrolled",
                    "from_version": 5, "source_label": "Enrollment pace", "measure": "net enrolled students", "label": "Enrolled",
                    "params": {"term": "Spring 2027"}, "format": "auto"}
    assert "ref" not in cell
    asked = []
    monkeypatch.setattr(artifact_access, "can_view", lambda conn, slug, subject: asked.append((slug, subject)) or subject == "will@x")
    resolved = sa.resolve(object(), "session", sa.validate({"title": "Mix", "width": 12, "cells": [value_cell()]}), "will@x")
    assert resolved["cells"][0]["member_kind"] == "value" and resolved["cells"][0]["member_title"] == "Enrolled"
    assert resolved["members"] == [] and asked == [("paid-media", "will@x")]
    with pytest.raises(ValueError, match="not an artifact you can open"):
        sa.resolve(object(), "session", sa.validate({"title": "Mix", "width": 12, "cells": [value_cell()]}), "mallory@x")


@pytest.mark.parametrize("bad, message", [
    ({"artifact": "Not A Slug"}, "artifact slug"),
    ({"object": "1bad"}, "value id"),
    ({"params": {"term": {"deep": 1}}}, "must be text"),
    ({"format": "roman"}, "format must be"),
    ({"w": 1}, "smaller than a value cell"),
])
def test_value_blocks_are_validated(bad, message):
    with pytest.raises(ValueError, match=message):
        sa.validate({"title": "Mix", "width": 12, "cells": [value_cell(**bad)]})


def test_a_grid_block_is_a_value_cell_with_a_grid_shape():
    clean = sa.validate({"title": "Mix", "width": 12, "cells": [value_cell(shape="grid", w=8, h=5, object="enrolled_by_month")]})
    assert clean["cells"][0]["shape"] == "grid"
    assert "shape" not in sa.validate({"title": "Mix", "width": 12, "cells": [value_cell()]})["cells"][0]
    with pytest.raises(ValueError, match="smaller than a grid cell"):
        sa.validate({"title": "Mix", "width": 12, "cells": [value_cell(shape="grid", w=3, h=1)]})
    with pytest.raises(ValueError, match="value or grid"):
        sa.validate({"title": "Mix", "width": 12, "cells": [value_cell(shape="pie")]})
