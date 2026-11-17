"""Meaning narrative worker (meaning_jobs.py): JSON extraction, output shaping,
budget gate, and the two work kinds driven with a fake model and a fake
connection. The SQL side (packets, clusters, apply, Jev review) is exercised
on the pilot."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("WAREHOUSE_DSN", "postgresql://x")

import meaning_jobs  # noqa: E402


def test_extract_json_object_handles_fences_prose_and_nested_braces():
    assert meaning_jobs.extract_json_object('{"a": 1}') == {"a": 1}
    assert meaning_jobs.extract_json_object('```json\n{"a": {"b": "}"}}\n```') == {"a": {"b": "}"}}
    assert meaning_jobs.extract_json_object('Sure! Here it is:\n{"product": "Salesforce", "objects": []}\nHope that helps') == {"product": "Salesforce", "objects": []}
    with pytest.raises(ValueError):
        meaning_jobs.extract_json_object("no json here")
    with pytest.raises(ValueError):
        meaning_jobs.extract_json_object("[1, 2, 3]")


def test_normalize_recognition_keeps_only_known_tables_and_valid_roles():
    raw = {
        "product": "Salesforce Sales Cloud", "confidence": "0.9",
        "objects": [
            {"table": "salesforce.lead", "business_name": "Lead", "role": "entity", "package": None, "one_line": "One prospective student."},
            {"table": "made_up", "business_name": "Ghost", "role": "entity"},
            {"table": "smagicinteract_conversation_c", "business_name": "SMS Conversation", "role": "weird", "package": "SMS Magic"},
        ],
        "expected_joins": [
            {"from": "lead", "column": "owner_id", "to": "user"},
            {"from": "lead", "column": "ghost_id", "to": "made_up"},
            {"from": "task", "column": "who_id", "to": "lead", "to_column": "id"},
        ],
        "standard_questions": ["leads created per week", "", 42],
        "ignore": ["lead_toggle_c", "nope"],
    }
    out = meaning_jobs.normalize_recognition(raw, {"lead", "user", "task", "smagicinteract_conversation_c", "lead_toggle_c"})
    assert out["product"] == "Salesforce Sales Cloud" and out["confidence"] == 0.9
    assert [o["table"] for o in out["objects"]] == ["lead", "smagicinteract_conversation_c"]   # dotted name stripped, ghost dropped
    assert out["objects"][1]["role"] is None and out["objects"][1]["package"] == "SMS Magic"
    assert out["expected_joins"] == [{"from": "lead", "column": "owner_id", "to": "user", "to_column": "id"},
                                     {"from": "task", "column": "who_id", "to": "lead", "to_column": "id"}]
    assert out["standard_questions"] == ["leads created per week", "42"]
    assert out["ignore"] == ["lead_toggle_c"]


def test_normalize_domain_bounds_lists():
    raw = {"name": "Salesforce · Lead pipeline", "about": "x" * 2000,
           "questions": [f"q{i}" for i in range(10)],
           "candidate_metrics": [{"name": f"m{i}", "formula": "count(*)", "grain": "per day"} for i in range(12)] + [{"formula": "no name"}],
           "concerns": ["a", "b", "c", "d", "e"]}
    out = meaning_jobs.normalize_domain(raw)
    assert len(out["about"]) == 1200 and len(out["questions"]) == 6 and len(out["candidate_metrics"]) == 8 and len(out["concerns"]) == 4
    assert out["candidate_metrics"][0] == {"name": "m0", "formula": "count(*)", "grain": "per day"}


class _FakeClient:
    backend_name = "openrouter"
    model = "google/gemini-3.8-flash"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete_json(self, *, system, user, max_tokens):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        reply = self.replies.pop(0)
        return reply, {"model_version": "google/gemini-3.8-flash", "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}


class _FakeConn:
    """Answers the worker's SQL by substring, records writes."""
    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def transaction(self):
        return self

    def execute(self, sql, params=None):
        self.store.setdefault("sql", []).append((sql, params))
        self._sql = sql
        self._params = params
        return self

    def fetchone(self):
        sql = self._sql
        if "meaning_reserve_model_call" in sql:
            return {"id": "00000000-0000-0000-0000-000000000001"}
        if "meaning_recognition_packet" in sql:
            return {"p": {"schema": "salesforce", "source_system": "salesforce", "relations": [{"table": "lead"}, {"table": "user"}]}}
        if "meaning_recognition_fingerprint" in sql:
            return {"f": "fp1"}
        if "meaning_apply_recognition" in sql:
            self.store["applied"] = json.loads(self._params[1])
            return {"v": {"objects_applied": 1, "joins": {"verified": 1}}}
        if "meaning_unit_packet" in sql:
            return {"p": {"schema": "salesforce", "tables": [{"table": "lead"}, {"table": "task"}]}}
        if "meaning_review_domain" in sql:
            return {"r": {"makes_sense": 0.8, "kind": "domain", "usable": True}}
        if "meaning_narrative_spend" in sql:
            return {"s": self.store.get("spent", 0)}
        return None

    def fetchall(self):
        if "meaning_narrative_queue" in self._sql:
            return self.store.get("queue", [])
        return []


def test_recognition_round_trip_applies_normalized_output_and_records_cost():
    store = {}
    client = _FakeClient(['```json\n{"product": "Salesforce", "confidence": 0.9, "objects": [{"table": "lead", "business_name": "Lead", "role": "entity", "one_line": "One prospect."}], "expected_joins": [{"from": "lead", "column": "owner_id", "to": "user"}], "standard_questions": ["leads per week"]}\n```'])
    result = meaning_jobs.run_recognition(lambda: _FakeConn(store), client, "salesforce")
    assert result["status"] == "done" and result["objects"] == 1
    assert store["applied"]["objects"][0]["business_name"] == "Lead"
    assert store["applied"]["expected_joins"] == [{"from": "lead", "column": "owner_id", "to": "user", "to_column": "id"}]
    insert = [s for s in store["sql"] if "INSERT INTO rvbbit.meaning_recognitions" in s[0]][0]
    assert insert[1][0] == "salesforce" and insert[1][2] == "fp1" and insert[1][7] == 1000 and insert[1][8] == 200
    assert 0 < insert[1][9] < 0.01                              # estimated cost from tokens
    assert "SOURCE PACKET" in client.calls[0]["user"] and "OUTPUT CONTRACT" in client.calls[0]["user"]


def test_recognition_repairs_once_then_records_an_error():
    store = {}
    client = _FakeClient(["not json", "still not json"])
    result = meaning_jobs.run_recognition(lambda: _FakeConn(store), client, "salesforce")
    assert result["status"] == "error" and "JSON" in result["error"]
    assert len(client.calls) == 2 and client.calls[1]["user"].endswith("Return exactly one JSON object and nothing else.")
    assert any("status = 'error'" in s[0] for s in store["sql"])


def test_domain_round_trip_reviews_with_jev():
    store = {}
    client = _FakeClient(['{"name": "Salesforce · Lead pipeline", "about": "Leads and the tasks logged against them.", "questions": ["how many leads this week"], "candidate_metrics": [{"name": "Leads created", "formula": "count of lead rows by created_date", "grain": "per day"}]}'])
    result = meaning_jobs.run_domain(lambda: _FakeConn(store), client, "meaning:salesforce:abc", "salesforce", ["salesforce.lead", "salesforce.task"], "fp")
    assert result["status"] == "done" and result["review"]["usable"] is True
    apply = [s for s in store["sql"] if "meaning_apply_domain" in s[0]][0]
    assert apply[1][0] == "meaning:salesforce:abc" and apply[1][2] == ["salesforce.lead", "salesforce.task"]
    assert json.loads(apply[1][4])["name"] == "Salesforce · Lead pipeline"


def test_tick_respects_the_daily_budget_and_orders_recognition_first():
    store = {"spent": 9.5}
    out = meaning_jobs.tick(lambda: _FakeConn(store), _FakeClient([]), budget_usd=5.0)
    assert out == [{"status": "budget", "spent_24h_usd": 9.5, "budget_usd": 5.0}]
    store = {"spent": 0.1, "queue": [
        {"kind": "recognition", "key": "salesforce", "schema_name": "salesforce", "members": None, "member_fingerprint": None, "reason": "new"},
        {"kind": "domain", "key": "meaning:salesforce:abc", "schema_name": "salesforce", "members": ["salesforce.lead"], "member_fingerprint": "fp", "reason": "new"},
    ]}
    client = _FakeClient(['{"product": "Salesforce", "objects": [], "expected_joins": []}',
                          '{"name": "Salesforce · Leads", "about": "Leads.", "questions": [], "candidate_metrics": []}'])
    out = meaning_jobs.tick(lambda: _FakeConn(store), client, budget_usd=5.0)
    assert [r["status"] for r in out] == ["done", "done"]
    assert "SOURCE PACKET" in client.calls[0]["user"] and "CLUSTER PACKET" in client.calls[1]["user"]


def test_normalize_map_caps_subjects_and_places_each_table_once():
    known = {"salesforce.lead", "salesforce.task", "marts.mart_lead_funnel_daily", "facebook_ads.basic_ad"}
    raw = {"company": "Vocational dental training", "subjects": [
        {"key": "Admissions & lead pipeline", "name": "Admissions & lead pipeline", "about": "Leads to enrollments.", "questions": ["leads this week"],
         "members": [{"table": "salesforce.lead", "role": "core"}, {"table": "marts.mart_lead_funnel_daily", "role": "support"},
                     {"table": "ghost.table", "role": "core"}, "salesforce.task"]},
        {"name": "Paid media", "members": [{"table": "facebook_ads.basic_ad", "role": "weird"}, {"table": "salesforce.lead", "role": "core"}]},
        {"name": "", "members": []},
    ] + [{"name": f"extra {i}", "members": []} for i in range(20)],
        "unplaced": ["salesforce.event", "facebook_ads.basic_ad"]}
    out = meaning_jobs.normalize_map(raw, known)
    assert out["company"] == "Vocational dental training"
    assert len(out["subjects"]) == 15                                            # capped
    first = out["subjects"][0]
    assert first["key"] == "admissions_lead_pipeline"
    assert [m["table"] for m in first["members"]] == ["salesforce.lead", "marts.mart_lead_funnel_daily", "salesforce.task"]
    assert first["members"][2]["role"] == "core"                                 # bare string member → core
    second = out["subjects"][1]
    assert second["members"] == [{"table": "facebook_ads.basic_ad", "role": "core"}]   # unknown role → core; lead already placed
    assert out["unplaced"] == []                                                 # unknown or already-known-only filtering


def test_normalize_dossier_keeps_member_roles_and_core_order():
    members = {"salesforce.lead", "salesforce.task", "salesforce.nbavs_user_c"}
    raw = {"about": "The pipeline.", "core": ["salesforce.lead", "salesforce.task", "other.thing"],
           "roles": {"salesforce.nbavs_user_c": "configuration", "salesforce.lead": "core", "ghost": "core", "salesforce.task": "bogus"},
           "areas": [{"name": "Outreach", "about": "SMS and calls", "tables": ["salesforce.task", "nope"]}],
           "questions": ["q"], "candidate_metrics": [{"name": "Leads", "formula": "count(lead.id)"}], "gaps": ["g1"]}
    out = meaning_jobs.normalize_dossier(raw, members)
    assert out["core"] == ["salesforce.lead", "salesforce.task"]
    assert out["roles"] == {"salesforce.nbavs_user_c": "configuration", "salesforce.lead": "core", "salesforce.task": "core"}
    assert out["areas"] == [{"name": "Outreach", "about": "SMS and calls", "tables": ["salesforce.task"]}]


class _MapConn(_FakeConn):
    def fetchone(self):
        sql = self._sql
        if "meaning_map_packet" in sql:
            return {"p": {"sources": [{"schema": "salesforce", "objects": [{"table": "lead"}]}]}}
        if "INSERT INTO rvbbit.meaning_company_map" in sql:
            return {"map_id": 7}
        if "meaning_apply_map" in sql:
            self.store["map_applied"] = json.loads(self._params[1])
            return {"a": {"subjects": 1, "placed": 1}}
        if "meaning_assign_unplaced" in sql:
            return {"a": {"assigned": 3, "unclear": 1}}
        if "meaning_subject_packet" in sql:
            return {"p": {"subject": "Admissions", "tables": [{"table": "salesforce.lead"}]}}
        if "SELECT members, rvbbit.meaning_subject_fingerprint" in sql:
            return {"members": ["salesforce.lead", "salesforce.task"], "fingerprint": "subject-fp"}
        if "meaning_subject_fingerprint" in sql:
            return {"fingerprint": "subject-fp"}
        if "meaning_apply_dossier" in sql:
            self.store["dossier_applied"] = json.loads(self._params[1])
            return {"a": {"core": 2, "members": 2}}
        if "meaning_review_domain" in sql:
            return {"r": {"makes_sense": 0.8, "core_tables": 2, "usable": True}}
        if "meaning_narrative_queue" in sql and "kind = 'dossier'" in sql:
            return {"n": 0}
        if "meaning_reconcile_subjects" in sql:
            return {"r": {"folded": 0}}
        return super().fetchone()

    def fetchall(self):
        if "FROM rvbbit.meaning_relations WHERE status = 'judged'" in self._sql:
            return [{"relation": "salesforce.lead"}, {"relation": "salesforce.task"}]
        return super().fetchall()


def test_map_round_trip_applies_subjects_then_assigns_the_tail():
    store = {}
    client = _FakeClient(['{"company": "Dental school", "subjects": [{"name": "Admissions & lead pipeline", "members": [{"table": "salesforce.lead", "role": "core"}, {"table": "salesforce.task", "role": "support"}]}]}'])
    out = meaning_jobs.run_map(lambda: _MapConn(store), client, "fp-map")
    assert out["status"] == "done" and out["subjects"] == 1 and out["assigned"] == {"assigned": 3, "unclear": 1}
    assert store["map_applied"]["subjects"][0]["members"][1] == {"table": "salesforce.task", "role": "support"}
    assert "COMPANY DATA ESTATE" in client.calls[0]["user"]
    done = [s for s in store["sql"] if "status = 'done'" in s[0] and "meaning_company_map" in s[0]]
    assert done and done[0][1][-1] == 7


def test_dossier_round_trip_and_tick_dispatch():
    store = {}
    client = _FakeClient(['{"about": "Leads become students.", "core": ["salesforce.lead"], "roles": {"salesforce.task": "support"}, "questions": ["q"], "candidate_metrics": [], "areas": [], "gaps": ["confirm lead statuses"]}'])
    out = meaning_jobs.run_dossier(lambda: _MapConn(store), client, "subject:admissions")
    assert out["status"] == "done" and out["review"]["usable"] is True
    assert store["dossier_applied"]["core"] == ["salesforce.lead"] and store["dossier_applied"]["roles"] == {"salesforce.task": "support", "salesforce.lead": "core"}
    store = {"spent": 0.1, "queue": [{"kind": "map", "key": "company", "schema_name": None, "members": None, "member_fingerprint": "fp", "reason": "new"}]}
    client = _FakeClient(['{"company": "x", "subjects": [{"name": "Admissions", "members": [{"table": "salesforce.lead", "role": "core"}]}]}'])
    out = meaning_jobs.tick(lambda: _MapConn(store), client, budget_usd=5.0)
    assert out[0]["status"] == "done" and out[0]["map_id"] == 7


# ── Resolution Ledger court (0451): proposals become cards, answers become verbs ──

class _CourtConn:
    """Scripted connection: answers SELECTs from a table of canned rows and
    records every statement so the test can assert what the court did."""

    def __init__(self, script):
        self.script = script
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        for needle, rows in self.script:
            if needle in sql:
                result = rows(params) if callable(rows) else rows
                return type("C", (), {
                    "fetchall": lambda _s, r=result: list(r),
                    "fetchone": lambda _s, r=result: (r[0] if r else None),
                })()
        return type("C", (), {"fetchall": lambda _s: [], "fetchone": lambda _s: None})()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _court_factory(conn):
    return lambda: conn


def _finding(**over):
    base = {
        "id": "11111111-1111-1111-1111-111111111111", "term_norm": "leads", "referent_key": "metric:enrollment_leads",
        "referent": {"kind": "metric", "name": "enrollment_leads"},
        "evidence": {"mentions": 14, "distinct_users": 3, "dominance": 0.86},
        "owner_email": "ryan@example.com",
    }
    base.update(over)
    return base


def test_court_turns_a_proposal_into_a_card_and_remembers_it(monkeypatch):
    import meaning_jobs
    import calliope_decisions
    created = []
    monkeypatch.setattr(calliope_decisions, "create_from_observation",
                        lambda *a, **k: created.append((a, k)) or {"id": "dddddddd-dddd-dddd-dddd-dddddddddddd", "status": "open"})
    conn = _CourtConn([
        ("FROM rvbbit.calliope_term_binding_findings", [_finding()]),
        ("purpose='setup'", [{"id": "55555555-5555-5555-5555-555555555555"}]),
        ("INSERT INTO rvbbit.calliope_observations", [{"id": "0bbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}]),
        ("d.status='answered'", []),
    ])
    out = meaning_jobs.run_ledger_court(_court_factory(conn))
    assert out["cards"] == 1 and out["errors"] == []
    (args, kwargs), = created
    assert args[1] == "55555555-5555-5555-5555-555555555555"      # grounded to the owner's setup session
    assert args[2] == "0bbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"      # to the observation the court just wrote
    assert "leads" in args[3] and "enrollment_leads" in args[3]  # the question names term and referent
    assert [o["key"] for o in args[4]] == ["approve", "set_aside"]
    assert kwargs["decision_kind"] == "approval" and kwargs["recommended_option"] == "approve"
    assert kwargs["context"]["finding_id"] == _finding()["id"]
    # The observation is company-scoped and idempotent on its fingerprint.
    obs_sql, obs_params = next((s, p) for s, p in conn.executed if "INSERT INTO rvbbit.calliope_observations" in s)
    assert "'company',NULL,'term_binding_proposal'" in obs_sql and "ON CONFLICT" in obs_sql
    assert obs_params[0] == "term_binding:leads|metric:enrollment_leads"
    # The finding now points at both, so nobody is asked twice.
    upd = next((s, p) for s, p in conn.executed if "SET decision_id=" in s)
    assert upd[1][:2] == ("dddddddd-dddd-dddd-dddd-dddddddddddd", "0bbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


def test_court_applies_answers_with_the_ledger_verbs():
    import meaning_jobs
    conn = _CourtConn([
        ("decision_id IS NULL", []),
        ("d.status='answered'", [
            {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "term_norm": "leads", "selected_option": "approve",
             "answered_by": "ryan@example.com", "response_note": ""},
            {"id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "term_norm": "spend", "selected_option": "set_aside",
             "answered_by": "juno@example.com", "response_note": "means ad spend, not payroll"},
        ]),
    ])
    out = meaning_jobs.run_ledger_court(_court_factory(conn))
    assert out["affirmed"] == 1 and out["declined"] == 1 and out["errors"] == []
    verbs = [(s, p) for s, p in conn.executed if "term_binding_affirm" in s or "term_binding_decline" in s]
    assert "term_binding_affirm" in verbs[0][0] and verbs[0][1][:2] == ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "ryan@example.com")
    assert "term_binding_decline" in verbs[1][0] and verbs[1][1] == ("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", "juno@example.com", "means ad spend, not payroll")


def test_court_skips_what_it_cannot_ground_and_survives_a_bad_card(monkeypatch):
    import meaning_jobs
    import calliope_decisions
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("decisions service down")
        return {"id": "dddddddd-dddd-dddd-dddd-dddddddddddd"}

    monkeypatch.setattr(calliope_decisions, "create_from_observation", flaky)
    conn = _CourtConn([
        ("FROM rvbbit.calliope_term_binding_findings", [
            _finding(id="11111111-1111-1111-1111-111111111111", owner_email=""),            # nobody to ask
            _finding(id="22222222-2222-2222-2222-222222222222", term_norm="pipeline"),     # card creation fails once
            _finding(id="33333333-3333-3333-3333-333333333333", term_norm="churn"),        # then works
        ]),
        ("purpose='setup'", []),
        ("ORDER BY updated_at DESC", [{"id": "55555555-5555-5555-5555-555555555555"}]),
        ("INSERT INTO rvbbit.calliope_observations", [{"id": "0bbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"}]),
        ("d.status='answered'", []),
    ])
    out = meaning_jobs.run_ledger_court(_court_factory(conn))
    assert out["skipped"] == 1 and out["cards"] == 1
    assert len(out["errors"]) == 1 and out["errors"][0].startswith("pipeline:")
