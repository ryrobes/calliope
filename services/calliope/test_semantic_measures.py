"""The semantic compiler keeps what is measured apart from the slice it is taken over."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


def _object(**meaning):
    return {
        "id": "enrolled_students",
        "meaning": {"label": "Fall 2026 enrolled", **meaning},
        "parameters": {
            "term": {"type": "text", "default": "Fall 2026", "dimension": "cohort", "fixed": True},
            "state": {"type": "text", "default": "All", "source": "#state", "dimension": "Place"},
            "odd": {"type": "text", "default": "x", "dimension": "weather"},
            "plain": {"type": "integer", "default": 2},
        },
        "bindings": [{"selector": "#enrolled"}],
        "evaluator": {"sql": "select count(*) as value from s where term={{term}} and ({{state}}='All' or state={{state}}) "
                             "and {{odd}}='x' and n >= {{plain}}"},
    }


def test_measure_conditions_and_slices_survive_normalization():
    out = server._normalize_semantic_object(_object(
        measure="  Net Enrolled   Students! ", conditions=["Deleted records excluded.", "", "Has an enrollment date."],
        asked_as="how many kids signed up"))
    assert out["meaning"]["measure"] == "net enrolled students"
    assert out["meaning"]["conditions"] == ["Deleted records excluded.", "Has an enrollment date."]
    assert out["meaning"]["asked_as"] == "how many kids signed up"
    params = out["parameters"]
    assert params["term"]["dimension"] == "cohort" and params["term"]["fixed"] is True and "source" not in params["term"]
    assert params["state"]["dimension"] == "place" and params["state"]["source"] == "#state"
    assert params["odd"]["dimension"] == "other"
    assert "dimension" not in params["plain"] and "fixed" not in params["plain"]
    # A fixed slice is still an ordinary parameter: its default renders, another value replaces it.
    assert "term='Fall 2026'" in server._render_semantic_sql(out)[0]
    assert "term='Spring 2027'" in server._render_semantic_sql(out, {"term": "Spring 2027"})[0]


def test_objects_without_the_new_fields_are_unchanged():
    out = server._normalize_semantic_object(_object())
    assert set(out["meaning"]) == {"label"}
    assert server._normalize_semantic_object(_object(conditions="One rule."))["meaning"]["conditions"] == ["One rule."]


def test_a_measure_or_slice_change_is_a_different_definition():
    base = server._normalize_semantic_object(_object(measure="enrolled students"))
    renamed = server._normalize_semantic_object(_object(measure="net enrolled students"))
    assert base["definition_hash"] != renamed["definition_hash"]


def test_business_context_keeps_the_newest_turns_inside_a_budget():
    turns = [{"artifact_version": n, "asked_by": "sam", "asked": f"ask {n} " + "x" * 3000, "answered": "a" * 3000}
             for n in range(1, 21)]
    packet = server._semantic_business_context_packet(
        turns, [{"term": "leads", "means": "metric salesforce_submitted_leads"}],
        {"salesforce.lead": {"what": "Lead"}, "big.table": {"what": "y" * 30_000}},
        [{"measure": "enrolled students", "used_by_artifacts": 3}],
        known_slices=[{"name": "start_date", "dimension": "time", "type": "date", "used_by_artifacts": 17}], budget=24_000)
    assert packet["known_slices"] == [{"name": "start_date", "dimension": "time", "type": "date", "used_by_artifacts": 17}]
    versions = [t["artifact_version"] for t in packet["conversation"]]
    assert versions == sorted(versions) and versions[-1] == 20 and len(versions) < 12
    assert all(len(t["asked"]) <= 1600 and len(t["answered"]) <= 900 for t in packet["conversation"])
    assert packet["agreed_terms"][0]["term"] == "leads" and packet["known_measures"][0]["measure"] == "enrolled students"
    assert list(packet["sources"]) == ["salesforce.lead"]
    assert len(json.dumps(packet)) < 24_000
    assert server._semantic_business_context_packet([], [], {}, []) == {}
    assert "conversation" not in server._semantic_business_context_packet([{"answered": "only a reply"}], [], {}, [])


def test_source_relations_come_from_the_queries_the_page_ran():
    queries = [{"sql": "select count(*) from salesforce.lead l join salesforce.person_c p on p.id = l.person_id"},
               {"sql": "select 1 from salesforce.lead"}, {"sql": "not sql at all ((("}, {"sql": ""}]
    assert server._semantic_source_relations(queries) == ["salesforce.lead", "salesforce.person_c"]


def test_credential_words_are_matched_whole_not_inside_ordinary_words():
    sensitive = server._SEMANTIC_SENSITIVE_RE.search
    for name in ("auth_token", "api_key", "apikeys", "session_cookie", "user_password", "authorization_header", "secrets"):
        assert sensitive(name), name
    for name in ("authority_location_count", "author_count", "tokenized_rows", "sessional_students", "secretary_calls"):
        assert not sensitive(name), name
    out = server._normalize_semantic_object({**_object(), "id": "authority_location_count"})
    assert out["id"] == "authority_location_count"
