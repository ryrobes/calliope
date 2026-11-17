"""Meaning layer, serving side (docs/MEANING_PLAN.md §3).

The card lives in SQL (migration 0439, exercised on the pilot); these tests pin
how it reaches the agent: describe_table carries a compact card and per-column
roles, find hits carry the one-line `what`, the turn-time packet carries cards
for tables the question names, and search_business_context no longer fails on
guessed status words.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import calliope  # noqa: E402
import server  # noqa: E402

CARD = {
    "relation": "salesforce.lead", "name": "lead", "kind": "entity_master",
    "what": "One row per lead from salesforce (225,279 rows, 346 columns, 291 custom fields). Keyed by id. Time: created_date.",
    "keys": ["id"], "time": {"column": "created_date", "grain": "per_row_timestamp"},
    "relates": {"joins_to": [{"via": "owner_id", "to": "salesforce.user", "on": "id", "state": "verified", "match": 0.98},
                             {"via": "account_id", "to": "salesforce.account", "on": "id", "state": "unverified"}],
                "joined_by": [{"from": "salesforce.task", "via": "who_id", "state": "verified", "match": 0.7}]},
    "measured_by": {"cubes": ["salesforce_lead_lifecycle_cube"], "dashboards": ["closer-performance"]},
    "deserves_metrics": "already measured",
    "personal_data": ["first_name", "phone", "email"],
    "scores": {"metric_worthy": 0.82, "plumbing": 0.06, "pii": 0.92, "customized": 0.92},
    "drift": {"rows": 225279, "rows_pct": 0.0, "flags": [], "severity": 0},
    "domain": {"key": "subject:admissions_and_leads", "name": "Admissions & lead pipeline", "role": "core", "usable": True,
               "about": "Prospective student acquisition, intake qualification and automated outreach.",
               "areas": [{"name": "Core CRM pipeline", "tables": ["salesforce.lead"]}]},
    "confidence": 0.97,
    "provenance": {"judged_by": "typesafe/jev-1.13-20260917", "status": "judged",
                   "note": "provisional: described from structure and a small judge; not affirmed canon"},
}


class _Cur:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last = sql
        return self

    def _pick(self):
        for needle, value in self.answers:
            if needle in self._last:
                return value
        return None

    def fetchone(self):
        value = self._pick()
        return value[0] if isinstance(value, list) else value

    def fetchall(self):
        value = self._pick()
        return value if isinstance(value, list) else ([] if value is None else [value])


class _Conn:
    def __init__(self, cur):
        self.cur = cur

    def __enter__(self):
        return self.cur

    def __exit__(self, *exc):
        return False


def test_meaning_summary_is_compact_and_marks_provisional(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": CARD})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("salesforce", "lead")
    assert out["what"].startswith("One row per lead")
    assert out["kind"] == "entity_master" and out["keys"] == ["id"]
    assert out["joins_to"] == ["owner_id → salesforce.user", "account_id → salesforce.account (unverified)"]
    assert out["joined_by"] == ["salesforce.task.who_id"]
    assert out["measured_by"] == {"cubes": ["salesforce_lead_lifecycle_cube"]}      # dashboards left out
    assert out["deserves_metrics"] == "already measured"
    assert out["personal_data"] == ["first_name", "phone", "email"]
    assert "drift" not in out                                                       # nothing flagged → nothing said
    assert out["provisional"] is True and "not affirmed" in out["note"]
    assert out["subject"] == {"name": "Admissions & lead pipeline", "role": "core", "usable": True,
                              "about": "Prospective student acquisition, intake qualification and automated outreach.",
                              "areas": ["Core CRM pipeline"]}
    assert len(str(out)) < 1200


def test_meaning_summary_absent_layer_or_unjudged_relation_is_silent(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": False})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server._meaning_summary("salesforce", "lead") is None
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": None})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server._meaning_summary("salesforce", "lead") is None

    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(server, "_conn", boom)
    assert server._meaning_summary("salesforce", "lead") is None
    assert server._meaning_column_roles("salesforce", "lead") == {}


def test_column_roles_join_target_and_personal_data(monkeypatch):
    cur = _Cur([("to_regclass('rvbbit.meaning_columns')", {"ok": True}),
                ("FROM rvbbit.meaning_columns", [
                    {"col_name": "owner_id", "role": "reference", "ref_target": "salesforce.user", "pii": 0.1},
                    {"col_name": "email", "role": "free_text", "ref_target": None, "pii": 0.95},
                    {"col_name": "status", "role": "category", "ref_target": None, "pii": None},
                ])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    roles = server._meaning_column_roles("Salesforce", "Lead")
    assert cur.calls[-1][1] == ("salesforce.lead",)                                  # relation key is lowered
    assert roles == {"owner_id": {"role": "reference", "refers_to": "salesforce.user"},
                     "email": {"role": "free_text", "personal_data": True},
                     "status": {"role": "category"}}


def test_find_hits_carry_the_card_line(monkeypatch):
    cur = _Cur([("to_regclass('rvbbit.meaning_relations')", {"ok": True}),
                ("FROM rvbbit.meaning_relations", [
                    {"relation": "salesforce.lead", "what": "One row per lead from salesforce.", "kind": "entity_master",
                     "confidence": 0.97, "name": "lead"}])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    matches = [
        {"object": "salesforce.lead.company", "kind": "db_column", "meaning": {"domains": [{"key": "leads"}]}},
        {"object": "salesforce.lead", "kind": "db_table"},
        {"object": "cubes.other_cube", "kind": "cube"},
    ]
    out = server._attach_meaning_to_hits(matches)
    assert next(p for q, p in cur.calls if "FROM rvbbit.meaning_relations WHERE status" in q) == (["cubes.other_cube", "salesforce.lead"],)
    assert out[0]["meaning"]["domains"] == [{"key": "leads"}]                        # existing refs kept
    assert out[0]["meaning"]["what"] == "One row per lead from salesforce."
    assert out[1]["meaning"]["relation_kind"] == "entity_master" and out[1]["meaning"]["provisional"] is True
    assert "meaning" not in out[2]                                                   # unjudged: untouched
    assert server._attach_meaning_to_hits([]) == []


def test_search_business_context_ignores_guessed_statuses(monkeypatch):
    """A quarter of the pilot's calls failed with INVALID_STATUS on words like
    'review'. Unknown words are dropped and reported; an all-unknown list falls
    back to the default set instead of erroring."""
    seen = {}

    class _C:
        def execute(self, sql, params=None):
            seen.setdefault("sql", []).append(sql)
            if "business_context_search(" in sql:
                seen["statuses"] = params[2]
            return self

        def fetchone(self):
            return {"stale_ok": False, "embed_ok": False, "crawled_at": None, "node_count": 0,
                    "embedded_count": 0, "last_error": None}

        def fetchall(self):
            return []

    class _Ctx:
        def __enter__(self):
            return _C()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Ctx())
    monkeypatch.setattr(server, "_business_context_relevance", lambda c, q: None, raising=False)
    out = server.tool_search_business_context("leads by source", 5, ["promoted", "review"])
    assert "error" not in out
    assert seen["statuses"] == ["promoted"]
    assert out.get("ignored_statuses") == ["review"]
    out = server.tool_search_business_context("leads by source", 5, "observed, provisional")
    assert "error" not in out
    assert seen["statuses"] == ["promoted", "approved", "proposed"]


def test_packet_carries_cards_for_tables_the_question_names():
    rows = [{"relation": "salesforce.lead", "rel_name": "lead", "name": "lead",
             "what": "One row per lead from salesforce.", "kind": "entity_master", "time_column": "created_date",
             "entity_keys": ["id"], "confidence": 0.97, "drift_flags": ["rows_up"], "subject": "Admissions & lead pipeline", "subject_role": "core",
             "joins": ["salesforce.user via owner_id"], "measured_by": ["salesforce_lead_lifecycle_cube"]}]
    cur = _Cur([("to_regclass('rvbbit.meaning_relations')", {"ok": True}), ("FROM rvbbit.meaning_relations m", rows)])
    cards = calliope._meaning_cards_for_question(lambda: _Conn(cur), "how many rows landed in salesforce.lead this week?")
    tokens = cur.calls[-1][1][0]
    assert "salesforce.lead" in tokens and "lead" in tokens                          # dotted name and its bare tail
    assert cards == [{"relation": "salesforce.lead", "name": "lead", "what": "One row per lead from salesforce.",
                      "kind": "entity_master", "subject": "Admissions & lead pipeline", "subject_role": "core",
                      "time_column": "created_date", "keys": ["id"],
                      "joins": ["salesforce.user via owner_id"], "measured_by": ["salesforce_lead_lifecycle_cube"],
                      "drift_flags": ["rows_up"], "confidence": 0.97, "provisional": True}]
    assert calliope._meaning_cards_for_question(lambda: _Conn(cur), "") == []
    note = calliope._business_context_instruction_note({"value_systems": [], "relations": cards})
    assert "provisional" in note and "salesforce.lead" in note
    assert calliope._business_context_instruction_note({"value_systems": [], "relations": []}) == ""


# ── the meaning tree (migration 0442): nouns with counts, served compact ──────

NOUN = {
    "relation": "salesforce.lead", "name": "Lead", "kind": "entity_master", "role": "core", "count": 225279,
    "what": "An unconverted prospective student or prospective dental clinic partner inquiry", "time": "created_date",
    "dimensions": [{"name": "status", "values": 9, "top": [{"v": "Working", "n": 120}]}, {"name": "original_source_c", "values": 31},
                   {"name": "lead_source", "values": 22}, {"name": "campus_c", "values": 12}, {"name": "rating", "values": 3}],
    "measures": ["minutes_to_initial_contact_c", "number_of_touches_c", "pricing_ask_count_c", "re_engagement_count_c", "times_email_5_sent_c"],
    "has": [{"name": "Unchained SMS Message", "relation": "salesforce.unchained_sms_message_c", "count": 4467125, "kind": "event_log", "via": "lead_c", "per_parent": 19.4},
            {"name": "Task", "relation": "salesforce.task", "count": 1822028, "kind": "event_log", "via": "who_id", "per_parent": 6.1}]
           + [{"name": f"Other {i}", "relation": f"x.o{i}", "count": 10 - i} for i in range(6)],
    "points_at": [{"name": "User", "relation": "salesforce.user", "via": "owner_id", "relationship": "many_to_one"},
                  {"name": "Person", "relation": "salesforce.person_c", "via": "person_c", "relationship": "one_to_one"}],
    "metrics": [{"name": "Average Minutes to Initial Contact", "formula": "AVG(minutes_to_initial_contact_c)", "grain": "Lead", "subject": "Admissions & lead pipeline"}],
    "derived_metrics": [{"name": "Lead count", "kind": "count", "formula": "count of rows in salesforce.lead by created_date", "grain": "per period (created_date)"},
                        {"name": "Lead by status", "kind": "breakdown", "formula": "count of salesforce.lead grouped by status, original_source_c", "grain": "per status"},
                        {"name": "Unchained Sms Message per lead", "kind": "per_entity", "formula": "count of salesforce.unchained_sms_message_c per lead_c (≈19.4 per lead)", "grain": "per lead"}],
    "measured_by": ["salesforce_lead_lifecycle_cube"],
}
TREE = {
    "subject": "Admissions & lead pipeline", "key": "subject:admissions_and_leads", "about": "Prospective student acquisition.",
    "tables": 170, "nouns": 2, "roles": {"core": 10, "support": 140, "plumbing": 20}, "usable": True,
    "questions": ["How many leads did we get this week?"],
    "metrics": [{"name": "Average Minutes to Initial Contact", "formula": "AVG(minutes_to_initial_contact_c)", "grain": "Lead", "why": "speed to lead"}],
    "gaps": ["no cost per lead without the ad tables"],
    "areas": [{"name": "Core CRM Pipeline & Pursuits", "about": "The pipeline.", "nouns": [NOUN], "other_tables": 13},
              {"name": "Other", "nouns": [dict(NOUN, relation="salesforce.smagicinteract_sms_magic_c", name="SMS Magic Message", kind="event_log", count=2600927, has=None, dimensions=None)]}],
}


def test_meaning_tree_compact_keeps_the_shape_and_cuts_the_fat():
    out = server._meaning_tree_compact(TREE)
    assert out["subject"] == "Admissions & lead pipeline" and out["tables"] == 170 and out["roles"]["core"] == 10
    assert [a["name"] for a in out["areas"]] == ["Core CRM Pipeline & Pursuits", "Other"]
    lead = out["areas"][0]["nouns"][0]
    assert lead["name"] == "Lead" and lead["count"] == 225279 and lead["kind"] == "entity_master" and lead["role"] == "core"
    assert lead["dimensions"] == ["status (9)", "original_source_c (31)", "lead_source (22)"]   # three, with value counts
    assert "measures" not in lead                                                # the full noun has them; derived metric names carry the gist
    assert lead["has"][0] == {"name": "Unchained SMS Message", "count": 4467125, "per_parent": 19.4} and len(lead["has"]) == 4
    assert lead["points_at"] == ["User via owner_id", "Person via person_c"]
    assert lead["metrics"] == ["Average Minutes to Initial Contact"]
    assert lead["derived_metrics"] == ["Lead count", "Lead by status", "Unchained Sms Message per lead"]
    assert "what" not in lead and "top" not in str(lead)                       # prose and top values stay in the full noun
    assert out["candidate_metrics"] == [{"name": "Average Minutes to Initial Contact", "formula": "AVG(minutes_to_initial_contact_c)", "grain": "Lead"}]
    assert out["questions"] == ["How many leads did we get this week?"] and out["gaps"] == ["no cost per lead without the ad tables"]
    assert out["areas"][1]["nouns"][0]["name"] == "SMS Magic Message" and "has" not in out["areas"][1]["nouns"][0]
    assert len(str(out)) < 2500


def test_meaning_tree_compact_shows_24_nouns_and_names_the_rest():
    many = dict(TREE, areas=[{"name": "A", "nouns": [dict(NOUN, relation=f"x.t{i}", name=f"Noun {i}") for i in range(30)]}])
    out = server._meaning_tree_compact(many)
    assert len(out["areas"][0]["nouns"]) == 24
    assert out["more_nouns"] == [f"Noun {i} (x.t{i})" for i in range(24, 30)]
    assert "more_nouns" not in server._meaning_tree_compact(TREE)


def test_meaning_tree_tool_lists_subjects_then_serves_a_subject_then_a_noun(monkeypatch):
    subjects = [{"key": "subject:admissions_and_leads", "name": "Admissions & lead pipeline", "tables": 170, "core": 10, "usable": True,
                 "rows": 15059098, "top_nouns": ["Lead", "Person"]}]
    cur = _Cur([("to_regprocedure('rvbbit.meaning_tree", {"ok": True}),
                ("rvbbit.meaning_subjects()", {"subjects": subjects}),
                ("FROM rvbbit.meaning_domains", {"domain_key": "subject:admissions_and_leads", "name": "Admissions & lead pipeline"}),
                ("rvbbit.meaning_tree(", {"tree": TREE}),
                ("rvbbit.meaning_noun(", {"noun": NOUN})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)

    listing = server.tool_meaning_tree()
    assert listing["subjects"] == subjects and listing["provisional"] is True and "meaning_tree(subject=" in listing["hint"]

    tree = server.tool_meaning_tree(subject="admissions")
    assert tree["subject"] == "Admissions & lead pipeline" and tree["provisional"] is True
    assert tree["areas"][0]["nouns"][0]["has"][0]["per_parent"] == 19.4
    sql, params = next((s, p) for s, p in cur.calls if "FROM rvbbit.meaning_domains" in s)
    assert "origin = 'map'" in sql and params == ("admissions",) * 4       # key, subject:key, or a name fragment

    full = server.tool_meaning_tree(subject="admissions", detail="full")
    assert full["areas"][0]["nouns"][0]["dimensions"][0]["top"] == [{"v": "Working", "n": 120}]

    noun = server.tool_meaning_tree(table="salesforce.lead")
    assert noun["noun"]["name"] == "Lead" and noun["provisional"] is True
    assert next(p for s, p in cur.calls if "rvbbit.meaning_noun(" in s) == ("salesforce.lead",)


def test_meaning_tree_tool_is_honest_when_absent_or_unknown(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_tree", {"ok": False})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server.tool_meaning_tree()["error"]["code"] == "MEANING_TREE_ABSENT"
    cur = _Cur([("to_regprocedure('rvbbit.meaning_tree", {"ok": True}), ("FROM rvbbit.meaning_domains", None)])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_meaning_tree(subject="nonsense")
    assert out["error"]["code"] == "SUBJECT_NOT_FOUND" and "no arguments" in out["error"]["hint"]
    cur = _Cur([("to_regprocedure('rvbbit.meaning_tree", {"ok": True}), ("rvbbit.meaning_noun(", {"noun": None})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    assert server.tool_meaning_tree(table="salesforce.nope")["error"]["code"] == "NOT_JUDGED"


def test_packet_cards_carry_counts_and_what_hangs_off_them():
    calls = []

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            calls.append((sql, params))
            self._sql = sql
            return self

        def fetchone(self):
            return {"ok": True}

        def fetchall(self):
            return [{"relation": "salesforce.lead", "rel_name": "lead", "name": "Lead", "what": "A prospective student inquiry", "kind": "entity_master",
                     "time_column": "created_date", "entity_keys": ["id"], "confidence": 0.97, "drift_flags": None, "subject_role": "core", "n_rows": 225279,
                     "subject": "Admissions & lead pipeline", "joins": ["salesforce.user via owner_id"], "measured_by": None,
                     "has": [{"name": "Unchained SMS Message", "rows": 4467125, "per_row": 19.4}, {"name": "Task", "rows": 1822028, "per_row": None}]}]

    cards = calliope._meaning_cards_for_question(lambda: Conn(), "how many leads got an sms this week? salesforce.lead")
    assert cards[0]["rows"] == 225279
    assert cards[0]["has"] == [{"name": "Unchained SMS Message", "rows": 4467125, "per_row": 19.4}, {"name": "Task", "rows": 1822028}]
    sql = next(s for s, _ in calls if "meaning_links" in s)
    assert "LIMIT 3" in sql and "l.match_rate" in sql


# ── vocabulary federation and statements that stick (migration 0443) ────────────

GOVERNED = [{"term": "leads", "via": "ledger", "status": "stipulated", "taught_by": "operator",
             "gloss": "When we say Leads we mean Salesforce Submission Events on qualifying People.",
             "referent": {"kind": "metric", "name": "salesforce_submitted_leads"},
             "metric": {"name": "salesforce_submitted_leads", "version": 5, "grain": "one scalar count",
                        "canonical_for": ["leads", "lead volume"], "not": ["Salesforce Lead"],
                        "source_objects": ["salesforce.submission_event_c", "salesforce.person_c"]}}]
EXTRAS = {"succession": {"superseded_by": [{"by": "salesforce.person_c", "effective_from": "2026-08-10",
                                           "note": "Person replaced Lead; history before the cutover is backfill."}]},
          "caveats": [{"id": 7, "text": "Counts are partial for today."}],
          "vocabulary": {"term_collisions": [{"term": "leads", "means": "metric salesforce_submitted_leads", "gloss": "…"}],
                         "governed_metrics": [{"metric": "salesforce_lead_lifecycle", "version": 2}]}}


def test_find_puts_the_governed_term_above_the_tables_with_what_it_excludes(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_governed_terms", {"ok": True}),
                ("rvbbit.meaning_governed_terms(", {"j": GOVERNED})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    hits = server._governed_terms_for("how many leads did we get this week")
    assert hits[0]["kind"] == "governed_term" and hits[0]["object"] == "metric salesforce_submitted_leads v5"
    assert hits[0]["metric"]["not"] == ["Salesforce Lead"] and hits[0]["metric"]["canonical_for"] == ["leads", "lead volume"]
    assert "metric(name='salesforce_submitted_leads')" in hits[0]["use"]
    assert hits[0]["why"].startswith('the business term "leads" is bound to metric salesforce_submitted_leads')
    cur = _Cur([("to_regprocedure('rvbbit.meaning_governed_terms", {"ok": False})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server._governed_terms_for("leads") == []                                   # older database: nothing added


def test_find_hits_carry_succession_collisions_and_caveats(monkeypatch):
    cur = _Cur([("to_regclass('rvbbit.meaning_relations')", {"ok": True}),
                ("FROM rvbbit.meaning_relations WHERE status", [{"relation": "salesforce.lead", "what": "Lead objects", "kind": "entity_master", "confidence": 0.9, "name": "Lead"}]),
                ("to_regprocedure('rvbbit.meaning_card_extras", {"ok": True}),
                ("rvbbit.meaning_card_extras(r)", [{"relation": "salesforce.lead", "extras": EXTRAS}])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._attach_meaning_to_hits([{"object": "salesforce.lead", "kind": "db_table"}])
    m = out[0]["meaning"]
    assert m["succession"]["superseded_by"][0]["by"] == "salesforce.person_c" and m["succession"]["superseded_by"][0]["since"] == "2026-08-10"
    assert m["term_collisions"] == [{"term": "leads", "means": "metric salesforce_submitted_leads", "gloss": "…"}]
    assert m["caveats"] == ["Counts are partial for today."]


def test_meaning_summary_passes_statements_through_compactly(monkeypatch):
    card = dict(CARD, **EXTRAS)
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": card})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("salesforce", "lead")
    assert out["succession"] == {"superseded_by": [{"by": "salesforce.person_c", "since": "2026-08-10", "note": "Person replaced Lead; history before the cutover is backfill."}]}
    assert out["caveats"] == [{"id": 7, "text": "Counts are partial for today."}]
    assert "caveats_more" not in out
    assert out["vocabulary"]["term_collisions"][0]["means"] == "metric salesforce_submitted_leads"
    assert out["vocabulary"]["governed_metrics"] == [{"metric": "salesforce_lead_lifecycle", "version": 2}]


def test_teach_meaning_validates_and_banks_on_a_persons_word(monkeypatch):
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    assert server.tool_teach_meaning("salesforce.person_c", "colour", "x", "r")["error"]["code"] == "BAD_FIELD"
    assert server.tool_teach_meaning("salesforce.person_c", "caveat", "partial today", "")["error"]["code"] == "REASON_REQUIRED"
    assert server.tool_teach_meaning("salesforce.person_c", "succession", "{not json", "r")["error"]["code"] == "BAD_VALUE"
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 42}),
                ("rvbbit.meaning_card(", {"card": {"name": "Person", "what": "A person", "kind": "entity_master",
                                                    "succession": {"succeeds": {"supersedes": ["salesforce.lead"], "effective_from": "2026-08-10", "note": "n"}}}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("salesforce.person_c", "succession",
                                    '{"supersedes": ["salesforce.lead"], "effective_from": "2026-08-10", "note": "n"}',
                                    "Ryan said so in chat, 2026-09-19")
    assert out["stated"]["statement_id"] == 42 and out["stated"]["actor"] == "calliope"
    assert out["card_now"]["succession"] == {"supersedes": ["salesforce.lead"], "since": "2026-08-10", "federation": None, "note": "n"} or out["card_now"]["succession"]["supersedes"] == ["salesforce.lead"]
    sql, params = next((s, p) for s, p in cur.calls if "SELECT rvbbit.meaning_state(%s" in s)
    assert params[0] == "salesforce.person_c" and params[1] == "succession" and params[4] is None and params[5] == "calliope"
    assert json.loads(params[2])["supersedes"] == ["salesforce.lead"]


def test_packet_cards_carry_succession_collisions_and_caveats():
    extras = json.dumps(EXTRAS)
    assert calliope._packet_succession(extras) == {"superseded_by": [{"by": "salesforce.person_c", "since": "2026-08-10", "note": "Person replaced Lead; history before the cutover is backfill."}]}
    assert calliope._packet_collisions(extras) == [{"term": "leads", "means": "metric salesforce_submitted_leads"}]
    assert calliope._packet_caveats(extras) == ["Counts are partial for today."]
    assert calliope._packet_succession(None) is None and calliope._packet_collisions("{}") is None


# ── context economy: lean describes on wide tables, a budget on find results ────────

def test_wide_tables_are_described_lean_unless_asked_otherwise(monkeypatch):
    cols = [{"name": f"c{i}", "type": "text"} for i in range(120)]

    class Cur:
        def __init__(self):
            self.sql = ""

        def __enter__(self):
            return self

        def __exit__(self, *e):
            return False

        def cursor(self):
            return self

        def execute(self, sql, params=None):
            self.sql = sql
            return self

        def fetchall(self):
            return cols if "information_schema.columns" in self.sql else []

        def fetchone(self):
            return None

    monkeypatch.setattr(server, "_ro", lambda *a, **k: Cur())
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    monkeypatch.setattr(server, "_samples", lambda cur, s, r, n: [{"c0": "sample"}])
    monkeypatch.setattr(server, "_col_stats", lambda cur, s, r, max_cols=128: {"c0": {"ndv": 3, "top": ["a", "b"]}})
    monkeypatch.setattr(server, "_freshness", lambda cur, s, r: None)
    monkeypatch.setattr(server, "_meaning_summary", lambda s, r, **kw: None)
    monkeypatch.setattr(server, "_meaning_column_roles", lambda s, r: {})
    auto = server.tool_describe_table("salesforce.person_c")
    assert "samples" not in auto and "top" not in auto["column_stats"]["c0"] and "120 columns" in auto["note"]
    full = server.tool_describe_table("salesforce.person_c", lean=False)
    assert full["samples"] == [{"c0": "sample"}] and full["column_stats"]["c0"]["top"] == ["a", "b"] and "note" not in full
    assert "samples" in server.tool_describe_table("salesforce.person_c", detail="full")
    assert "samples" not in server.tool_describe_table("salesforce.person_c", lean="false", detail="lean")   # detail wins
    assert "samples" in server.tool_describe_table("salesforce.person_c", lean="false")               # the string models send
    assert "samples" not in server.tool_describe_table("salesforce.person_c", lean="true")
    assert server._describe_detail("auto", None) is None and server._describe_detail(None, "null") is None
    narrow_cols = cols[:10]
    monkeypatch.setattr(Cur, "fetchall", lambda self: narrow_cols if "information_schema.columns" in self.sql else [])
    small = server.tool_describe_table("salesforce.lead_role_c")
    assert small["samples"] == [{"c0": "sample"}] and "note" not in small                     # narrow tables stay full


def test_find_results_are_trimmed_to_a_budget_with_a_note():
    big = [{"object": f"s.t{i}", "kind": "db_column", "score": 0.5, "seams": {"dense": {"rank": i}},
            "about": {"example_values": [f"value-{j}" * 20 for j in range(30)]},
            "matched_values": [f"m{j}" * 30 for j in range(20)], "snippet": "x" * 2000,
            "lineage": {"system": "salesforce", "load": "mirror"}, "usage": {"touches": 3}} for i in range(12)]
    kept, note = server._find_fit_budget([dict(m, about=dict(m["about"])) for m in big], budget=6000)
    assert note and note.startswith("result trimmed to fit")
    assert len(json.dumps(kept)) <= 6000 and len(kept) >= 3
    assert len(kept[0]["about"]["example_values"]) == 4 and len(kept[0]["matched_values"]) == 4 and kept[0]["snippet"].endswith("…")
    small = [{"object": "s.t", "kind": "db_table", "score": 1}]
    assert server._find_fit_budget([dict(m) for m in small]) == (small, None)


# ── concepts and implied metrics (migrations 0444, 0445) ────────────────────────

CONCEPT = {
    "name": "Ad Daily Performance", "family": "Tiktok Ads", "canonical": "tiktok_ads.ad_report_daily", "kind": "transactional_fact",
    "count": 36311, "tables": 3,
    "members": [{"relation": "tiktok_ads.ad_report_daily", "name": "Ad Daily Performance", "count": 36311, "layer": "source", "role": "core"},
                {"relation": "tiktok_ads_tiktok_ads.tiktok_ads__ad_report", "name": "TikTok Ad Report", "count": 36311, "layer": "package",
                 "role": "support", "same_rows_as": "tiktok_ads.ad_report_daily"},
                {"relation": "tiktok_ads.campaign_report_daily", "name": "Campaign Daily Performance", "count": 3794, "layer": "source", "role": "support"}],
    "layers": {"source": 2, "package": 1},
    "metrics": [{"name": "Click-Through Rate (CTR)", "formula": "sum(clicks)::float / nullif(sum(impressions), 0)", "metric": "click_through_rate", "status": "governed"}],
    "derived_metrics": [{"name": "Total spend", "kind": "sum"}, {"name": "Average ctr", "kind": "avg"}],
}


def test_meaning_tree_compact_folds_concepts_and_marks_their_members():
    member = dict(NOUN, relation="tiktok_ads.campaign_report_daily", name="Campaign Daily Performance", role="support")
    canonical = dict(NOUN, relation="tiktok_ads.ad_report_daily", name="Ad Daily Performance")
    tree = dict(TREE, concepts=[CONCEPT], folded=3,
                derived=[{"name": "Total spend", "kind": "sum", "sources": 27, "canonical": "marts.mart_ad_spend_daily", "relations": ["marts.mart_ad_spend_daily", "x.y"]}],
                metrics=[{"name": "Cost Per Lead (CPL)", "formula": "sum(spend) / nullif(sum(leads_submitted), 0)", "grain": "date_day", "metric": "cost_per_lead", "status": "implied"}],
                areas=[{"name": "Paid", "nouns": [canonical, member, NOUN]}])
    out = server._meaning_tree_compact(tree)
    concept = out["concepts"][0]
    assert concept["name"] == "Ad Daily Performance" and concept["family"] == "Tiktok Ads" and concept["canonical"] == "tiktok_ads.ad_report_daily"
    assert concept["tables"] == 3 and out["folded_tables"] == 3
    assert concept["members"] == ["tiktok_ads.ad_report_daily (source)",
                                  "tiktok_ads_tiktok_ads.tiktok_ads__ad_report (package, same rows as tiktok_ads.ad_report_daily)",
                                  "tiktok_ads.campaign_report_daily (source)"]
    assert concept["metrics"] == ["click_through_rate"] and concept["derived_metrics"] == ["Total spend", "Average ctr"]
    nouns = {n["relation"]: n for n in out["areas"][0]["nouns"]}
    assert "part_of" not in nouns["tiktok_ads.ad_report_daily"]                            # the canonical keeps its own card
    assert nouns["tiktok_ads.campaign_report_daily"]["part_of"] == "Ad Daily Performance"   # a member says so …
    assert "derived_metrics" not in nouns["tiktok_ads.campaign_report_daily"]              # … and leaves its derived metrics to the concept
    assert "derived_metrics" in nouns["salesforce.lead"]
    assert out["repeated_derived"] == [{"name": "Total spend", "sources": 27, "canonical": "marts.mart_ad_spend_daily"}]
    assert out["candidate_metrics"] == [{"name": "Cost Per Lead (CPL)", "formula": "sum(spend) / nullif(sum(leads_submitted), 0)",
                                         "grain": "date_day", "metric": "cost_per_lead", "status": "implied"}]


IMPLIED = [{"term": "cost per lead", "via": "metric_alias", "status": "implied", "taught_by": "meaning",
            "gloss": "Evaluates paid media efficiency. Implied by the meaning layer from reporting.fact_marketing_daily.",
            "referent": {"kind": "metric", "name": "cost_per_lead"},
            "metric": {"name": "cost_per_lead", "version": 1, "grain": "date_day, vendor, channel", "status": "implied",
                       "origin": "meaning", "subject": "Paid media & acquisition", "source_objects": ["reporting.fact_marketing_daily"]}}]


def test_find_says_when_an_implied_metric_answered_and_lowers_its_confidence(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_governed_terms", {"ok": True}),
                ("rvbbit.meaning_governed_terms(", {"j": IMPLIED})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    hits = server._governed_terms_for("what is our cost per lead this month")
    assert hits[0]["status"] == "implied" and hits[0]["metric"]["status"] == "implied" and hits[0]["metric"]["subject"] == "Paid media & acquisition"
    assert "metric(name='cost_per_lead')" in hits[0]["use"]
    assert "implied metric" in hits[0]["use"] and "reporting.fact_marketing_daily" in hits[0]["use"]
    assert "no person has confirmed it" in hits[0]["use"] and "never call it governed" in hits[0]["use"]
    governed = server._governed_terms_for("how many leads")                # a confirmed one keeps the old wording
    cur = _Cur([("to_regprocedure('rvbbit.meaning_governed_terms", {"ok": True}),
                ("rvbbit.meaning_governed_terms(", {"j": GOVERNED})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    governed = server._governed_terms_for("how many leads")
    assert governed[0]["use"].endswith("describe_table on its source objects for the shape") and "status" not in governed[0]["metric"]

    # through find: implied → confidence medium and an honest note; governed → high
    def fake_find(query, **kw):
        return {"query": query, "hits": [], "confidence": "none", "weak": True, "gates": {}, "elapsed_ms": 1}
    monkeypatch.setattr(server, "_find_json", fake_find, raising=False)
    monkeypatch.setattr(server, "_attach_meaning_to_hits", lambda m: m)
    for terms, confidence, phrase in ((IMPLIED, "medium", "an implied metric answered this"), (GOVERNED, "high", "the governed vocabulary answered this")):
        cur = _Cur([("to_regprocedure('rvbbit.meaning_governed_terms", {"ok": True}),
                    ("rvbbit.meaning_governed_terms(", {"j": terms}),
                    ("rvbbit.find_json(", {"j": {"query": "q", "hits": [], "confidence": "none", "weak": True, "gates": {}, "elapsed_ms": 1}})])
        monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
        monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
        out = server.tool_find("q")
        assert out["confidence"] == confidence, out
        assert phrase in out["note"], out["note"]
        assert out["gates"]["governed_terms"] == 1


def test_metric_tool_carries_the_provenance_of_an_implied_metric(monkeypatch):
    labels = {"status": "implied", "origin": "meaning", "subject_name": "Paid media & acquisition",
              "source_objects": ["reporting.fact_marketing_daily"], "formula": "sum(spend) / nullif(sum(leads_submitted), 0)"}
    cur = _Cur([("rvbbit.metric_scalar(", [{"m": {"value": 10.66, "value_column": "value"}}]),
                ("FROM rvbbit.metric_catalog WHERE name", {"labels": labels})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_metric("cost_per_lead")
    assert out["result"] == {"value": 10.66, "value_column": "value"}
    assert out["status"] == "implied" and out["origin"] == "meaning" and out["source"] == "reporting.fact_marketing_daily"
    assert out["subject"] == "Paid media & acquisition"
    assert out["note"].startswith("implied metric: the meaning layer registered it from reporting.fact_marketing_daily as sum(spend)")
    assert "no person has confirmed it" in out["note"] and "counts toward promotion" in out["note"]
    # a governed metric (no meaning origin) adds nothing; an older database without labels adds nothing
    cur = _Cur([("rvbbit.metric_scalar(", [{"m": {"value": 5}}]), ("FROM rvbbit.metric_catalog WHERE name", {"labels": {"proposed": True}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_metric("salesforce_submitted_leads")
    assert "status" not in out and "note" not in out
    cur = _Cur([("rvbbit.metric_scalar(", [{"m": {"value": 5}}])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert "status" not in server.tool_metric("x")


def test_teach_meaning_hands_back_caveat_ids_and_can_retire_a_wrong_line(monkeypatch):
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    # Writing a caveat: the reply carries each live caveat's id, so a correction can name the wrong one.
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 91}),
                ("rvbbit.meaning_card(", {"card": {"name": "Video Games", "what": "A game SKU", "kind": "entity_master",
                                                    "caveats": [{"id": 91, "text": "Gift cards are not volume."},
                                                                {"id": 77, "text": "Older wrong line."}]}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("shop.video_games", "caveat", "Gift cards are not volume.", "Ryan said so")
    assert out["stated"]["statement_id"] == 91
    assert out["card_now"]["caveats"] == [{"id": 91, "text": "Gift cards are not volume."}, {"id": 77, "text": "Older wrong line."}]
    assert out["no_card"] is None
    # Retiring: ownership is checked on the relation, the SQL verb is called with who and why, and the card is re-read.
    cur = _Cur([("FROM rvbbit.meaning_overrides WHERE override_id", {"override_id": 77}),
                ("rvbbit.meaning_retire_statement(", {"meaning_retire_statement": True}),
                ("rvbbit.meaning_card(", {"card": {"name": "Video Games", "caveats": [{"id": 91, "text": "Gift cards are not volume."}]}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("shop.video_games", "retire_caveat", "77", "superseded by the gift-card correction, per Ryan")
    assert out["retired"] == {"relation": "shop.video_games", "caveat_id": 77, "actor": "calliope"}
    assert [c["id"] for c in out["card_now"]["caveats"]] == [91]
    sql, params = next((s, p) for s, p in cur.calls if "meaning_retire_statement" in s)
    assert params[0] == 77 and params[1].startswith("calliope: superseded by the gift-card correction")
    own_sql, own_params = next((s, p) for s, p in cur.calls if "WHERE override_id" in s)
    assert own_params == (77, "shop.video_games")
    # A caveat that is not live on this relation cannot be retired by accident.
    cur = _Cur([("FROM rvbbit.meaning_overrides WHERE override_id", None)])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server.tool_teach_meaning("shop.video_games", "retire_caveat", "12345", "r")["error"]["code"] == "NOT_FOUND"
    assert server.tool_teach_meaning("shop.video_games", "retire_caveat", "not-an-id", "r")["error"]["code"] == "BAD_VALUE"


def test_teach_meaning_says_so_when_the_relation_has_no_card(monkeypatch):
    monkeypatch.setattr(server, "_schema_allowed", lambda s: True)
    # A cube is not carded by Meaning: the fact is stored, but no card will show it, and the reply must not pretend.
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 5}),
                ("rvbbit.meaning_card(", {"card": None})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("cubes.assortment", "caveat", "The cube retracts returns.", "Ryan said so")
    assert out["stated"]["statement_id"] == 5
    assert out["card_now"] is None
    assert out["no_card"]["relation"] == "cubes.assortment" and "no Meaning card" in out["no_card"]["reason"]


def test_lambda_registered_tools_advertise_their_underlying_contract():
    """FastMCP reads a tool's description from its callable's docstring. Most tools
    here are bare lambdas around tool_<name>, and shipped with no description at
    all; the registrar must carry the underlying contract across."""
    captured = {}

    class _FakeMcp:
        def tool(self, *args, **kwargs):
            def register(fn):
                captured[kwargs.get("name") or args[0]] = fn
                return fn
            return register

    reg = server._NonBlockingMcpRegistrar(_FakeMcp())
    # A bare lambda under a name that has a tool_<name> with a docstring.
    reg.tool(name="teach_meaning")(lambda relation, field, value, reason=None: None)
    assert (captured["teach_meaning"].__doc__ or "").strip() == (server.tool_teach_meaning.__doc__ or "").strip()
    assert "retire_caveat" in captured["teach_meaning"].__doc__
    # A def with its own docstring is left alone.
    def _mine():
        """My own words."""
    reg.tool(name="teach_meaning")(_mine)
    assert captured["teach_meaning"].__doc__.strip() == "My own words."
    # A lambda under a name with no tool_<name> behind it stays as it was.
    reg.tool(name="no_such_tool_xyz")(lambda: None)
    assert not (captured["no_such_tool_xyz"].__doc__ or "").strip()


LONG_CAVEAT = ("Gift card rows carry a price of zero and a rating of zero, so any average over the whole "
               "table is dragged down by them. Filter them out with category <> 'Gift Cards' before averaging; "
               "the 2026-09 refresh added about nine thousand of them. Bundles are a second problem: one parent_asin "
               "can stand for several physical items, so counts of rows are not counts of products either.")


def test_clip_statement_cuts_at_a_sentence_and_says_so():
    whole, cut = server._clip_statement("Short line.", 240)
    assert (whole, cut) == ("Short line.", False)
    text, cut = server._clip_statement(LONG_CAVEAT, 240)
    assert cut and text.endswith(" …") and len(text) <= 242
    assert text.startswith("Gift card rows") and text[:-2].rstrip().endswith(".")   # a whole sentence, not a fragment
    text, cut = server._clip_statement("x" * 500, 100)                            # no sentence, no space: hard cut, still marked
    assert cut and text == "x" * 100 + " …"
    assert calliope._clip_statement(LONG_CAVEAT, 200).endswith(" …")


def test_meaning_summary_cuts_caveats_honestly_and_full_returns_them_whole(monkeypatch):
    many = [{"id": i, "text": LONG_CAVEAT if i == 1 else f"Line {i}.", "as_of": "2026-09-21" if i == 1 else None} for i in range(1, 7)]
    card = dict(CARD, caveats=many)
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": card})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("salesforce", "lead")
    assert len(out["caveats"]) == 4 and out["caveats_more"] == 2
    first = out["caveats"][0]
    assert first["id"] == 1 and first["as_of"] == "2026-09-21" and first["cut"] is True and first["text"].endswith(" …")
    assert out["caveats"][1] == {"id": 2, "text": "Line 2."}                      # a whole line is not marked cut
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": card})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    full = server._meaning_summary("salesforce", "lead", full=True)
    assert len(full["caveats"]) == 6 and "caveats_more" not in full
    assert full["caveats"][0] == {"id": 1, "text": LONG_CAVEAT, "as_of": "2026-09-21"}   # what was stored comes back


def test_find_hits_and_packet_cards_say_how_many_caveats_they_left_out(monkeypatch):
    extras = dict(EXTRAS, caveats=[{"id": i, "text": LONG_CAVEAT if i == 1 else f"Line {i}."} for i in range(1, 6)])
    cur = _Cur([("to_regclass('rvbbit.meaning_relations')", {"ok": True}),
                ("FROM rvbbit.meaning_relations WHERE status", [{"relation": "salesforce.lead", "what": "Lead objects", "kind": "entity_master", "confidence": 0.9, "name": "Lead"}]),
                ("to_regprocedure('rvbbit.meaning_card_extras", {"ok": True}),
                ("rvbbit.meaning_card_extras(r)", [{"relation": "salesforce.lead", "extras": extras}])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    m = server._attach_meaning_to_hits([{"object": "salesforce.lead", "kind": "db_table"}])[0]["meaning"]
    assert len(m["caveats"]) == 3 and m["caveats_more"] == 2 and m["caveats"][0].endswith(" …") and m["caveats"][1] == "Line 2."
    raw = json.dumps(extras)
    assert len(calliope._packet_caveats(raw)) == 3 and calliope._packet_caveats_more(raw) == 2
    assert calliope._packet_caveats_more(json.dumps(EXTRAS)) is None


LENS = {"relation": "cubes.catalog_assortment", "name": "Catalog Assortment", "kind": "lens", "grain": "one row per catalog extract and main_category",
        "what": "Rollup of Arts, Movies & TV, Video Games products at one row per extract; 9 metrics read it.",
        "derives_from": [{"relation": "store.meta_video_games", "name": "Video Games products", "subject": "subject:product_reviews_voice"},
                         {"relation": "store.meta_movies_and_tv", "name": "Movies & TV products", "subject": "subject:product_reviews_voice"}],
        "spans": [{"key": "subject:product_reviews_voice", "name": "Product reviews & customer voice"}],
        "dimensions": ["catalog_extract", "main_category"], "measures": ["product_count", "rating_volume"], "time": None,
        "metrics": [{"name": "arts_catalog_product_count"}, {"name": "video_games_catalog_product_count"}],
        "refresh": {"cron": None, "at": "2026-09-22T00:27:08+00:00", "rows": 106}, "usage": {"touches_30d": 16}}
THROUGH = [{"lens": "cubes.catalog_assortment", "name": "Catalog Assortment", "grain": "one row per catalog extract and main_category",
            "metrics": ["video_games_catalog_product_count", "video_games_catalog_rating_volume", "video_games_well_counted_avg_rating"]}]


def test_meaning_summary_carries_the_lens_and_the_path_through_it(monkeypatch):
    cube = dict(CARD, relation="cubes.catalog_assortment", lens=LENS)
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": cube})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("cubes", "catalog_assortment")
    lens = out["lens"]
    assert lens["grain"].startswith("one row per") and lens["derives_from"] == ["store.meta_video_games", "store.meta_movies_and_tv"]
    assert lens["spans"] == ["Product reviews & customer voice"] and lens["metrics"] == ["arts_catalog_product_count", "video_games_catalog_product_count"]
    assert lens["refreshed_at"] == "2026-09-22T00:27:08+00:00" and lens["touches_30d"] == 16 and "measured_through" not in out
    source = dict(CARD, measured_through=THROUGH)
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": source})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("store", "meta_video_games")
    assert out["measured_through"] == ["cubes.catalog_assortment (3 metrics: video_games_catalog_product_count, video_games_catalog_rating_volume, video_games_well_counted_avg_rating)"]
    assert "lens" not in out


def test_compact_tree_lists_lenses_and_nouns_say_what_reads_them():
    tree = {"subject": "Product reviews & customer voice", "key": "subject:product_reviews_voice", "tables": 9, "nouns": 3,
            "areas": [{"name": "Catalog", "nouns": [{"name": "Video Games products", "relation": "store.meta_video_games", "count": 137233,
                                                     "measured_by": ["catalog_assortment"], "measured_through": THROUGH}]}],
            "lenses": [LENS]}
    out = server._meaning_tree_compact(tree)
    noun = out["areas"][0]["nouns"][0]
    assert noun["measured_through"][0].startswith("cubes.catalog_assortment (3 metrics")
    lens = out["lenses"][0]
    assert lens["name"] == "Catalog Assortment" and lens["relation"] == "cubes.catalog_assortment" and lens["derives_from"] == ["store.meta_video_games", "store.meta_movies_and_tv"]
    assert lens["metrics"] == ["arts_catalog_product_count", "video_games_catalog_product_count"] and lens["touches_30d"] == 16


def test_find_hits_and_packet_cards_include_derived_lenses(monkeypatch):
    cur = _Cur([("to_regclass('rvbbit.meaning_relations')", {"ok": True}),
                # the answer key requires the derived status in the query: a judged-only reader would find nothing here
                ("meaning_relations WHERE status IN ('judged', 'derived')", [{"relation": "cubes.catalog_assortment", "what": "Rollup of three catalog extracts.", "kind": "aggregate_or_cube", "confidence": 1, "name": "Catalog Assortment"}]),
                ("to_regprocedure('rvbbit.meaning_card_extras", {"ok": True}),
                ("rvbbit.meaning_card_extras(r)", [{"relation": "cubes.catalog_assortment", "extras": {"lens": LENS}}])])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._attach_meaning_to_hits([{"object": "cubes.catalog_assortment", "kind": "db_table"}])
    assert out[0]["meaning"]["what"] == "Rollup of three catalog extracts."
    extras = json.dumps({"lens": LENS, "measured_through": THROUGH})
    assert calliope._packet_lens(extras) == {"grain": "one row per catalog extract and main_category", "derives_from": ["store.meta_video_games", "store.meta_movies_and_tv"],
                                             "metrics": ["arts_catalog_product_count", "video_games_catalog_product_count"], "refreshed_at": "2026-09-22T00:27:08+00:00"}
    assert calliope._packet_measured_through(extras) == ["cubes.catalog_assortment (3 metrics)"]
    assert calliope._packet_lens(json.dumps(EXTRAS)) is None


def test_meaning_summary_serves_what_people_stated_about_an_uncarded_table(monkeypatch):
    stated = {"caveats": [{"id": 5, "text": "Re-submissions appear twice; count distinct person_id."}],
              "measured_through": THROUGH}
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": None}),
                ("rvbbit.meaning_card_extras(%s)", {"extras": stated})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._meaning_summary("public", "unified_leads")
    assert out["card"] == "none" and out["provisional"] is True and "stated" in out["note"]
    assert out["caveats"] == [{"id": 5, "text": "Re-submissions appear twice; count distinct person_id."}]
    assert out["measured_through"][0].startswith("cubes.catalog_assortment (3 metrics")
    assert "what" not in out and "deserves_metrics" not in out                      # nothing judged is invented
    assert any("meaning_card_extras(%s)" in sql and params == ("public.unified_leads",) for sql, params in cur.calls)
    # nothing stated either → still silent
    cur = _Cur([("to_regprocedure('rvbbit.meaning_card", {"ok": True}), ("rvbbit.meaning_card(", {"card": None}),
                ("rvbbit.meaning_card_extras(%s)", {"extras": {}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    assert server._meaning_summary("public", "unified_leads") is None


def test_capability_search_answers_object_shaped_queries_with_find(monkeypatch):
    # one identifier-shaped token names data; a sentence names an ability
    for q in ("aa_ads", "leads", "sales.orders", "Unified_Leads"):
        assert server._capability_query_looks_like_object(q), q
    for q in ("dashboard construction live app", "forecast a series", "", "a" * 65, "sales.orders.id", "means()"):
        assert not server._capability_query_looks_like_object(q), q
    seen = {}
    def fake_find(query, limit=8, schema=None, kinds=None, rerank=True, judge=False):
        seen.update(query=query, limit=limit, rerank=rerank)
        return {"matches": [{"object": "public.aa_ads", "kind": "db_table", "score": 0.91, "meaning": {"what": "Ad spend by day and campaign."}},
                            {"object": "public.aa_ads.spend", "kind": "db_column", "score": 0.4}],
                "degraded": []}
    monkeypatch.setattr(server, "tool_find", fake_find)
    data = server._capability_data_hits("aa_ads")
    assert seen == {"query": "aa_ads", "limit": 5, "rerank": False}
    assert data["hits"][0] == {"object": "public.aa_ads", "kind": "db_table", "score": 0.91, "what": "Ad spend by day and campaign."}
    assert data["hits"][1] == {"object": "public.aa_ads.spend", "kind": "db_column", "score": 0.4}
    assert "looks like a data object" in data["note"] and "find" in data["note"]
    monkeypatch.setattr(server, "tool_find", lambda *a, **k: {"matches": []})
    assert server._capability_data_hits("nothing_here") is None
    monkeypatch.setattr(server, "tool_find", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("find down")))
    assert server._capability_data_hits("aa_ads") is None                          # discovery never fails because find did


def test_teach_meaning_keys_are_column_lists_and_say_what_they_change(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 88}),
                ("rvbbit.meaning_card(", {"card": dict(CARD, keys=["parent_asin"])})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("store.meta_video_games", "keys", "parent_asin, extract", "Ryan: parent_asin identifies one catalog row")
    sql, params = next((q, p) for q, p in cur.calls if "SELECT rvbbit.meaning_state(" in q)
    assert params[1] == "keys" and json.loads(params[2]) == ["parent_asin", "extract"]
    assert out["stated"]["value"] == ["parent_asin", "extract"] and "re-enriched" in out["note"]
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 89}), ("rvbbit.meaning_card(", {"card": CARD})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("store.meta_video_games", "keys", ["parent_asin"], "Ryan said so")
    assert json.loads(next(p for q, p in cur.calls if "SELECT rvbbit.meaning_state(" in q)[2]) == ["parent_asin"]
    assert server.tool_teach_meaning("store.meta_video_games", "keys", " ", "Ryan said so")["error"]["code"] == "BAD_VALUE"


def test_capability_tool_sync_sends_first_paragraphs_and_recrawls_on_change(monkeypatch):
    index = [{"name": "teach_meaning", "description": "Bank a fact a person told you.\n\n    Second paragraph, not the summary.", "params": []},
             {"name": "metric", "description": "A blessed,\n    governed scalar number.", "params": []},
             {"name": "", "description": "no name", "params": []}] + [{"name": f"t{i}", "description": f"tool {i}", "params": []} for i in range(20)]
    tools = server._capability_tool_summaries(index)
    assert tools[0] == {"name": "teach_meaning", "summary": "Bank a fact a person told you."}
    assert tools[1] == {"name": "metric", "summary": "A blessed, governed scalar number."} and len(tools) == 22
    # a summary ends a sentence: the crawl appends " NOT a local SQL function — …" straight after it
    assert server._capability_tool_summaries([{"name": "x", "description": "retire it in DataRabbit"}])[0]["summary"] == "retire it in DataRabbit."
    assert server._capability_tool_summaries([{"name": "x", "description": "Ready?"}])[0]["summary"] == "Ready?"
    cur = _Cur([("to_regprocedure('rvbbit.capability_mcp_tools_sync", {"ok": True}),
                ("capability_mcp_tools_sync(%s::jsonb)", {"r": {"inserted": 1, "updated": 0, "removed": 0, "changed": True}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._sync_capability_mcp_tools(index)
    assert out["inserted"] == 1 and out["crawled"] is True
    sent = json.loads(next(p for q, p in cur.calls if "SELECT rvbbit.capability_mcp_tools_sync(" in q)[0])
    assert sent[0]["name"] == "teach_meaning" and any("capability_crawl(false)" in q for q, _ in cur.calls)
    assert server._sync_capability_mcp_tools(index[:3]) is None                        # a short list never touches the table


def test_remove_mcp_server_previews_then_removes_through_the_admin_path(monkeypatch):
    answers = [("FROM rvbbit.mcp_servers WHERE name", {"name": "fireflies", "transport": "stdio", "created_at": "2026-06-18"}),
               ("FROM rvbbit.mcp_tools WHERE server", {"n": 20}), ("FROM rvbbit.operators WHERE name LIKE", {"n": 20}),
               ("FROM pg_proc WHERE pronamespace", {"n": 20}), ("FROM rvbbit.capability_catalog WHERE id", {"active": True}),
               ("FROM rvbbit.catalog_docs WHERE graph_id", {"n": 21})]
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(_Cur(answers)))
    out = server._mcp_remove_mcp_server("sess-1", "fireflies")
    assert out["removed"] is False and out["preview"]["tools"] == 20 and out["preview"]["pack_active"] is True and "confirm=true" in out["note"]
    assert server._mcp_remove_mcp_server("sess-1", "../etc")["error"]["code"] == "BAD_NAME"
    # confirm: the fixed statement rides the administrator path, and the receipt shows what changed
    calls = {}
    class _Auth:
        subject = "alice@example.com"
    monkeypatch.setattr(server, "_require_application_subject", lambda: _Auth())
    monkeypatch.setattr(calliope, "is_enabled", lambda: True)
    def fake_admin(conn, session_id, sql, approved, authorized_owner=None):
        calls.update(sql=sql, session=session_id, owner=authorized_owner)
        answers[0] = ("FROM rvbbit.mcp_servers WHERE name", None)                     # gone afterwards
        for i in (1, 2, 3, 5):
            answers[i] = (answers[i][0], {"n": 0})
        answers[4] = ("FROM rvbbit.capability_catalog WHERE id", {"active": False})
        return {"ok": True}
    monkeypatch.setattr(calliope, "administer_local_sql_for_session", fake_admin)
    out = server._mcp_remove_mcp_server("sess-1", "fireflies", confirm=True)
    assert calls == {"sql": "SELECT rvbbit.drop_mcp_server('fireflies')", "session": "sess-1", "owner": "alice@example.com"}
    assert out["removed"] is True and out["before"]["operators"] == 20 and out["after"]["operators"] == 0 and out["receipt"] == {"ok": True}


def test_capability_tool_sync_forgets_and_embeds_after_the_crawl(monkeypatch):
    index = [{"name": f"t{i}", "description": f"tool {i}", "params": []} for i in range(24)]
    cur = _Cur([("to_regprocedure('rvbbit.capability_mcp_tools_sync", {"ok": True}),
                ("capability_mcp_tools_sync(%s::jsonb)", {"r": {"changed": True}}),
                ("to_regprocedure('rvbbit.capability_prune_stale", {"ok": True}), ("SELECT rvbbit.capability_prune_stale() AS r", {"r": {"docs": 235}}),
                ("to_regprocedure('rvbbit.capability_embed_missing", {"ok": True}), ("SELECT rvbbit.capability_embed_missing(1000) AS r", {"r": {"embedded": 863, "remaining": 0}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server._sync_capability_mcp_tools(index)
    assert out["crawled"] is True and out["pruned"] == {"docs": 235} and out["embedded"] == {"embedded": 863, "remaining": 0}
    order = [q for q, _ in cur.calls if "capability_" in q and "to_regprocedure" not in q]
    assert order == ["SELECT rvbbit.capability_mcp_tools_sync(%s::jsonb) AS r", "SELECT rvbbit.capability_crawl(false)",
                     "SELECT rvbbit.capability_prune_stale() AS r", "SELECT rvbbit.capability_embed_missing(1000) AS r"]



def test_teach_meaning_column_ignore_reaches_meaning_state_with_the_column(monkeypatch):
    """0457: a column-level statement (ignore/role/pii/ref_target) passes the column as p_col;
    the reply says what an ignored column stops feeding. Relation-only fields refuse a column."""
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 61}),
                ("rvbbit.meaning_card(", {"card": {"name": "Video Games Product Metadata", "what": "One parent_asin", "kind": "entity_master"}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("store.meta_video_games", "ignore", "true", "Ryan: the naive sum of price misleads", column="price")
    assert out["stated"]["column"] == "price" and out["stated"]["value"] is True and out["stated"]["statement_id"] == 61
    assert "suggested metrics" in out["note"] and "classification candidates" in out["note"]
    sql, params = next((s, p) for s, p in cur.calls if "SELECT rvbbit.meaning_state(%s" in s)
    assert params[0] == "store.meta_video_games" and params[1] == "ignore" and json.loads(params[2]) is True
    assert params[4] == "price" and params[5] == "calliope"
    assert server.tool_teach_meaning("store.meta_video_games", "caveat", "x", "r", column="price")["error"]["code"] == "BAD_FIELD"
    assert server.tool_teach_meaning("store.meta_video_games", "pii", "high", "r", column="email")["error"]["code"] == "BAD_VALUE"


def test_teach_meaning_domain_key_and_caveat_notes_say_the_worker_reads_them(monkeypatch):
    cur = _Cur([("to_regprocedure('rvbbit.meaning_state", {"ok": True}), ("rvbbit.meaning_state(", {"id": 62}),
                ("rvbbit.meaning_card(", {"card": {"name": "Products", "what": "One product", "kind": "entity_master"}})])
    monkeypatch.setattr(server, "_conn", lambda *a, **k: _Conn(cur))
    out = server.tool_teach_meaning("co.products", "domain_key", "subject:sales_catalog_promotions", "Ryan: it is the sales catalog, not reviews")
    assert "left every other subject" in out["note"] and "column" not in out["stated"]
    sql, params = next((s, p) for s, p in cur.calls if "SELECT rvbbit.meaning_state(%s" in s)
    assert params[4] is None


def test_compact_tree_keeps_breakdown_narrative_separate_from_scalar_metrics():
    breakdown={'name':'Sales mix','purpose':'Explain regional contributions.','measures':['SUM(amount)'],
               'dimensions':['region'],'grain':'Region','caveats':'Orders only.','legacy_metric_idea':{'name':'Old shape'}}
    out=server._meaning_tree_compact({**TREE,'breakdowns':[breakdown]})
    assert out['candidate_breakdowns']==[{k:v for k,v in breakdown.items() if k!='legacy_metric_idea'}]
    assert all(m['name']!='Sales mix' for m in out['candidate_metrics'])
