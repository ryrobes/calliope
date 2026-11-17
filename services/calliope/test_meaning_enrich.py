"""Meaning enrichment worker (meaning_jobs.run_enrich): proposal shaping, the
register → sample → judge → publish path, the one revision a weak proposal
gets, and the queue routing. The SQL side (packet, register, sample through
decision_tree_batch, publish) is exercised on the box; see
docs/MEANING_ENRICH_PLAN.md §8."""
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


PACKET = {
    "subject": "Earth & unusual event observations",
    "about": "Sightings and observations reported by the public.",
    "tables": [
        {"table": "public.ufo_sightings", "name": "UFO Sightings", "rows": 80000,
         "columns": [{"column": "comments", "role": "free_text"}, {"column": "shape", "role": "category"}],
         "sampled_values": {"comments": ["bright light moving fast", "triangle overhead"]}},
        {"table": "public.bigfoot_sightings", "name": "Bigfoot Sightings",
         "columns": [{"column": "observed", "role": "free_text"}]},
    ],
}


def _proposal(**over):
    base = {
        "relation": "public.ufo_sightings",
        "name": "account_type",
        "label_name": "Sighting account type",
        "columns": ["comments"],
        "question": {"kind": "choice", "instructions": "What kind of account is this?",
                     "criteria": {"lights_only": "only lights", "solid_craft": "a solid object"}},
        "why": "sorts accounts by what was seen",
        "expects": "compare with other observation tables",
    }
    base.update(over)
    return base


# ── shaping ────────────────────────────────────────────────────────────────
def test_normalize_enrich_keeps_only_tables_and_columns_the_packet_offered():
    raw = {"proposals": [
        _proposal(),                                                   # good
        _proposal(relation="public.invented_table", name="ghost"),     # table never offered
        _proposal(name="bad_col", columns=["nope", "comments"]),       # one bad column, one good
        _proposal(name="no_cols", columns=["nope"]),                   # nothing left
    ]}
    out = meaning_jobs.normalize_enrich(raw, PACKET)
    names = [p["name"] for p in out["proposals"]]
    assert names == ["account_type", "bad_col"]
    assert out["proposals"][1]["columns"] == ["comments"]              # the invented column is dropped, not the proposal


def test_normalize_enrich_caps_proposals_and_refuses_unusable_questions():
    raw = {"proposals": [
        _proposal(name="one_label", question={"kind": "choice", "instructions": "x", "criteria": {"only": "one"}}),
        _proposal(name="no_kind", question={"instructions": "x"}),
        _proposal(name="treeless", question={"kind": "tree", "instructions": "x"}),
        *[_proposal(name=f"fine_{i}") for i in range(6)],
    ]}
    out = meaning_jobs.normalize_enrich(raw, PACKET)
    assert [p["name"] for p in out["proposals"]] == [f"fine_{i}" for i in range(meaning_jobs.ENRICH_MAX_PROPOSALS)]


def test_normalize_enrich_shapes_truth_and_tree_questions():
    raw = {"proposals": [
        _proposal(name="is_close", question={"kind": "truth", "instructions": "Was it close?", "threshold": 9}),
        _proposal(name="staged", question={"kind": "tree", "instructions": "x", "tree": {"kind": "leaf", "label": "a"}}),
    ]}
    out = meaning_jobs.normalize_enrich(raw, PACKET)
    assert out["proposals"][0]["question"]["threshold"] == 0.95        # clamped, never passed through raw
    assert out["proposals"][1]["question"]["tree"] == {"kind": "leaf", "label": "a"}


def test_normalize_enrich_slugifies_names_and_drops_duplicates():
    raw = {"proposals": [_proposal(name="Account Type!!"), _proposal(name="account type"), _proposal(name="x")]}
    out = meaning_jobs.normalize_enrich(raw, PACKET)
    assert [p["name"] for p in out["proposals"]] == ["account_type"]   # same slug twice is one proposal; "x" is too short


# ── the pass ───────────────────────────────────────────────────────────────
class _Client:
    backend_name = "clover_llm"
    model = "calliope-sol"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete_json(self, *, system, user, max_tokens):
        self.calls.append({"system": system, "user": user})
        return self.replies.pop(0), {"model_version": "calliope-sol",
                                     "usage": {"prompt_tokens": 900, "completion_tokens": 300}}


class _Conn:
    """Answers the worker's SQL by substring and records every call."""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.store.setdefault("sql", []).append(sql)
        self._sql, self._params = sql, params
        # the worker fires these and never fetches, so record them here
        if "meaning_enrich_sentinel" in sql:
            self.store.setdefault("sentinels", []).append(params[1])
        elif "meaning_enrich_retire" in sql:
            self.store.setdefault("retired", []).append(params[0])
        return self

    def fetchone(self):
        sql, store = self._sql, self.store
        if "meaning_enrich_packet" in sql:
            return {"p": store.get("packet", PACKET)}
        if "meaning_enrich_register" in sql:
            store.setdefault("registered", []).append(json.loads(self._params[1]))
            if store.get("reject"):
                return {"r": {"rejected": store["reject"]}}
            return {"r": {"id": 42, "name": "account_type", "relation": "public.ufo_sightings"}}
        if "meaning_enrich_sample" in sql:
            return {"s": store.get("sample", {"rows": 500, "labeled": 500, "cost_usd": 0.0007,
                                              "distribution": [{"label": "a", "n": 300, "share": 0.6},
                                                               {"label": "b", "n": 200, "share": 0.4}]})}
        if "meaning_enrich_judge" in sql:
            verdicts = store.setdefault("verdicts", [{"keep": True, "score": 0.7}])
            return {"j": verdicts.pop(0) if len(verdicts) > 1 else verdicts[0]}
        if "meaning_enrich_publish" in sql:
            store.setdefault("published", []).append(self._params[0])
            return {"p": {"status": "published", "view": "meaning_labels.ufo_sightings__account_type", "labels": 2}}
        if "meaning_enrich_retire" in sql:
            return {"r": {"status": "retired"}}
        if "meaning_enrich_sentinel" in sql:
            return {"s": {"status": "sentinel"}}
        if "meaning_narrative_spend" in sql:
            return {"s": store.get("spent", 0)}
        if "meaning_enrich_spend" in sql:
            return {"s": store.get("enrich_spent", 0)}
        return None

    def fetchall(self):
        return self.store.get("queue", []) if "meaning_narrative_queue" in self._sql else []


def _factory(store):
    return lambda: _Conn(store)


def test_run_enrich_registers_samples_judges_and_publishes_in_order():
    store = {}
    client = _Client([json.dumps({"proposals": [_proposal()]})])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")

    assert out["status"] == "done" and out["published"] == 1 and out["dropped"] == 0
    steps = [s.split("rvbbit.meaning_enrich_")[1].split("(")[0] for s in store["sql"] if "rvbbit.meaning_enrich_" in s]
    assert steps == ["packet", "register", "sample", "judge", "publish", "sentinel"]
    assert store["published"] == [42]
    assert store["sentinels"] and "1 published" in store["sentinels"][-1]
    # the packet, the contract and the capability catalog all reach the model
    assert "SUBJECT PACKET" in client.calls[0]["user"] and "OUTPUT CONTRACT" in client.calls[0]["user"]


def test_run_enrich_writes_a_sentinel_and_asks_nothing_when_no_table_can_be_classified():
    store = {"packet": {"subject": "Empty", "tables": []}}
    client = _Client([])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:empty")
    assert out["status"] == "nothing_to_enrich"
    assert client.calls == []                                          # never spend a proposal call on nothing
    assert store["sentinels"] == ["no candidate columns"]


def test_run_enrich_records_the_models_own_reason_when_it_proposes_nothing():
    store = {}
    client = _Client([json.dumps({"proposals": [], "skipped": "every column already states its category"})])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert out["status"] == "none_proposed"
    assert store["sentinels"] == ["every column already states its category"]


def test_run_enrich_skips_a_rejected_proposal_without_sampling_it():
    store = {"reject": "relation_not_in_subject"}
    client = _Client([json.dumps({"proposals": [_proposal()]})])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert out["dropped"] == 1 and out["published"] == 0
    assert not any("meaning_enrich_sample" in s for s in store["sql"])


def test_a_proposal_whose_labels_do_not_split_the_data_gets_exactly_one_revision():
    store = {"verdicts": [{"keep": False, "reason": "one label holds 97% of the sample"}, {"keep": True, "score": 0.8}]}
    client = _Client([
        json.dumps({"proposals": [_proposal()]}),
        json.dumps({"proposals": [_proposal(name="account_type_v2",
                                            question={"kind": "choice", "instructions": "sharper",
                                                      "criteria": {"x": "one", "y": "two"}})]}),
    ])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")

    assert out["published"] == 1
    assert len(client.calls) == 2                                      # proposal, then one revision, never a third
    assert "did not split the data usefully" in client.calls[1]["system"]
    assert "one label holds 97%" in client.calls[1]["user"]
    assert store["registered"][1]["name"] == "account_type"            # the revision replaces the slot, never duplicates it
    assert store["registered"][1]["question"]["instructions"] == "sharper"


def test_a_proposal_that_fails_twice_is_retired_not_published():
    store = {"verdicts": [{"keep": False, "reason": "flat"}, {"keep": False, "reason": "still flat"}]}
    client = _Client([json.dumps({"proposals": [_proposal()]}),
                      json.dumps({"proposals": [_proposal(question={"kind": "choice", "instructions": "again",
                                                                    "criteria": {"x": "1", "y": "2"}})]})])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert out["published"] == 0 and out["dropped"] == 1
    assert store["retired"] == [42] and "published" not in store


def test_a_model_that_will_not_revise_drops_the_proposal_without_a_third_call():
    store = {"verdicts": [{"keep": False, "reason": "flat"}]}
    client = _Client([json.dumps({"proposals": [_proposal()]}),
                      json.dumps({"proposals": [], "skipped": "this column cannot be classified usefully"})])
    out = meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert out["published"] == 0 and store["retired"] == [42]
    assert len(client.calls) == 2


def test_a_failed_proposal_call_marks_the_subject_and_never_leaves_the_queue_spinning():
    class _Broken(_Client):
        def complete_json(self, **kwargs):
            raise RuntimeError("upstream 503")

    store = {}
    out = meaning_jobs.run_enrich(_factory(store), _Broken([]), "subject:observations")
    assert out["status"] == "error" and "upstream 503" in out["error"]
    assert store["sentinels"] and "upstream 503" in store["sentinels"][0]


# ── routing ────────────────────────────────────────────────────────────────
def test_tick_routes_the_enrich_kind_to_the_enrich_client(monkeypatch):
    store = {"queue": [{"kind": "enrich", "key": "subject:observations", "schema_name": None,
                        "members": None, "member_fingerprint": None, "reason": "new"}]}
    seen = {}
    monkeypatch.setattr(meaning_jobs, "run_enrich",
                        lambda factory, client, key: seen.setdefault("call", (client.model, key)) or {"status": "done"})
    client = _Client([])
    client._enrich_client = _Client([])
    client._enrich_client.model = "calliope-sol"
    meaning_jobs.tick(_factory(store), client, budget_usd=5)
    assert seen["call"] == ("calliope-sol", "subject:observations")


def test_tick_skips_enrich_over_its_own_budget_without_touching_the_narrative_budget(monkeypatch):
    store = {"queue": [{"kind": "enrich", "key": "s", "schema_name": None, "members": None,
                        "member_fingerprint": None, "reason": "new"}],
             "enrich_spent": 99.0}
    monkeypatch.setattr(meaning_jobs, "run_enrich", lambda *a, **k: pytest.fail("ran over budget"))
    results = meaning_jobs.tick(_factory(store), _Client([]), budget_usd=5)
    assert results == []


def test_tick_skips_an_unknown_queue_kind_instead_of_narrating_it(monkeypatch):
    # A queue-shape migration once deployed ahead of the worker turned unknown
    # kinds into cluster domains ("Unknown - Empty Cluster" rows).
    store = {"queue": [{"kind": "something_new", "key": "k", "schema_name": "s",
                        "members": [], "member_fingerprint": "f", "reason": "new"}]}
    monkeypatch.setattr(meaning_jobs, "run_domain", lambda *a, **k: pytest.fail("narrated an unknown kind"))
    assert meaning_jobs.tick(_factory(store), _Client([]), budget_usd=5) == []


def test_tick_still_routes_a_cluster_domain(monkeypatch):
    store = {"queue": [{"kind": "domain", "key": "meaning:x", "schema_name": "s",
                        "members": ["s.a"], "member_fingerprint": "f", "reason": "new"}]}
    monkeypatch.setattr(meaning_jobs, "run_domain", lambda *a, **k: {"status": "done", "domain_key": "meaning:x"})
    assert meaning_jobs.tick(_factory(store), _Client([]), budget_usd=5)[0]["domain_key"] == "meaning:x"

def test_the_first_dimensions_send_the_dossier_back_so_it_can_cite_them():
    # A fresh appliance writes every dossier before a label exists; without this
    # the dossier that would cite them is never rewritten.
    store = {}
    client = _Client([json.dumps({"proposals": [_proposal()]})])
    meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert any("SET status = 'placed'" in s for s in store["sql"])


def test_a_subject_that_already_has_dimensions_does_not_requeue_its_dossier():
    store = {"packet": dict(PACKET, existing=[{"table": "public.ufo_sightings", "name": "account_type"}])}
    client = _Client([json.dumps({"proposals": [_proposal(name="another")]})])
    meaning_jobs.run_enrich(_factory(store), client, "subject:observations")
    assert not any("SET status = 'placed'" in s for s in store["sql"])


def test_normalize_enrich_drops_names_retired_on_that_table():
    """A label a person withdrew stays withdrawn (0457): the packet's `retired` list is a
    do-not-re-propose list per table; the same name on another table is still fine."""
    packet = dict(PACKET, retired=[{"table": "public.ufo_sightings", "name": "account_type", "reason": "title-keyed, not trustworthy"}])
    raw = {"proposals": [
        _proposal(),
        _proposal(name="Account Type"),  # slugs to the same retired name
        _proposal(relation="public.bigfoot_sightings", name="account_type", columns=["observed"]),
        _proposal(name="sighting_time_of_day"),
    ]}
    out = meaning_jobs.normalize_enrich(raw, packet)
    assert [(p["relation"], p["name"]) for p in out["proposals"]] == [
        ("public.bigfoot_sightings", "account_type"), ("public.ufo_sightings", "sighting_time_of_day")]


def test_worker_prompts_tell_the_model_what_stated_and_retired_mean():
    """The packets carry `stated` (caveats, ignored columns) and, for the proposer, `retired`;
    the system prompts have to say those outrank the samples or the model treats them as trivia."""
    assert "`stated`" in meaning_jobs._DOSSIER_SYSTEM and "caveat" in meaning_jobs._DOSSIER_SYSTEM
    assert "`retired`" in meaning_jobs._ENRICH_SYSTEM and "`stated`" in meaning_jobs._ENRICH_SYSTEM
    assert "never propose one of those names" in meaning_jobs._ENRICH_SYSTEM
