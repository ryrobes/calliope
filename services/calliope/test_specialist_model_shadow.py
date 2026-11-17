"""Shadow selection never changes the model, handoff, or launch dependency."""
import asyncio
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import specialist_model_shadow as shadow


def run(**changes):
    return dict(id="run", round_no=1, task="Build the specified chart with validated SQL",
                profile_name="iris", source_session_id="session", owner_email="owner@test", context={}, **changes)


def answer(**changes):
    choices = dict(scope="bounded", evidence="supplied", verification="explicit", breadth="local", visual="none")
    choices.update(changes)
    return {"model": "test-jev", "usage": {"cost": .0001, "input_tokens": 100},
            "answers": {k: {"type": "choice", "choice": choices[k],
                "probabilities": {c: 1.0 if c == choices[k] else 0.0 for c in criteria}}
                for k, criteria in shadow.QUESTIONS.items()}}


@pytest.fixture(autouse=True)
def isolation(monkeypatch):
    monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW", "shadow")
    monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_OWNERS", "")
    monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_SESSIONS", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    assert not shadow._tasks
    yield
    assert not shadow._tasks


@pytest.mark.parametrize("choices,lane", [
    ({}, "luna"),
    ({"visual": "reference", "verification": "review"}, "luna"),
    ({"evidence": "lookup"}, "terra"),
    ({"scope": "mixed", "breadth": "multi"}, "terra"),
    ({"scope": "open"}, "sol"),
    ({"evidence": "investigate"}, "sol"),
    ({"visual": "invent"}, "sol"),
    ({"verification": "unclear"}, "sol"),
    ({"evidence": "unknown"}, "keep_current"),
])
def test_candidate_policy_is_explicit_and_conservative(choices, lane):
    inputs = shadow.packet("task", {}, 0)
    decisions = shadow.judgments(answer(**choices))
    assert shadow.recommendation(decisions, inputs)[0] == lane


def test_bounded_packet_excludes_unrelated_context_and_flags_incomplete_input():
    context = {"provided": {"spec": "x" * 9000}, "secret_soul": "not included",
               "current_turn": {"design_profile": {"compiled_prompt": "not included"}},
               shadow.KEY: {"recommendation": "luna"}}
    before = copy.deepcopy(context)
    inputs = shadow.packet("q" * 10000, context, 2)
    assert context == before
    assert set(inputs["truncated_fields"]) == {"task", "provided"}
    assert "not included" not in json.dumps(inputs)
    assert shadow.KEY not in json.dumps(inputs)
    assert inputs["image_count"] == 2 and inputs["design_profile_present"]
    assert shadow.recommendation(shadow.judgments(answer()), inputs) == ("keep_current", "truncated_handoff")
    wrapped = shadow.packet("task", {"provided": {"summary": "partial", "truncated": True}}, 0)
    assert wrapped["truncated_fields"] == ["provided"]


def test_uncertain_probabilities_abstain_and_malformed_answers_fail():
    response = answer()
    response["answers"]["scope"]["probabilities"].update(bounded=.6, mixed=.4)
    assert shadow.recommendation(shadow.judgments(response), shadow.packet("task", {}, 0))[0] == "keep_current"
    response["answers"]["scope"]["probabilities"]["bounded"] = float("nan")
    with pytest.raises(ValueError): shadow.judgments(response)
    with pytest.raises(ValueError): shadow.judgments({"answers": {}})
    response = answer()
    response["answers"]["scope"]["choice"] = "open"
    with pytest.raises(ValueError): shadow.judgments(response)


def install_storage(monkeypatch):
    receipts = {}
    def claim(factory, r, receipt):
        if r["id"] in receipts: return False
        receipts[r["id"]] = copy.deepcopy(receipt)
        return True
    def finish(factory, r, receipt): receipts[r["id"]] = copy.deepcopy(receipt)
    monkeypatch.setattr(shadow, "_claim", claim)
    monkeypatch.setattr(shadow, "_finish", finish)
    return receipts


def test_background_request_does_not_hold_launch_or_mutate_context_and_deduplicates(monkeypatch):
    saved = install_storage(monkeypatch)
    calls = []
    async def scenario():
        release = asyncio.Event()
        async def request(body, key):
            calls.append(body)
            await release.wait()
            return answer()
        monkeypatch.setattr(shadow, "request", request)
        context = {"provided": {"sql": "SELECT 1"}}
        before = copy.deepcopy(context)
        shadow.schedule(None, run(), context, "exact Hermes message")
        shadow.schedule(None, run(), context, "exact Hermes message")
        # The caller proceeds immediately, while inference remains blocked.
        assert context == before and shadow._tasks and not release.is_set()
        release.set()
        await asyncio.gather(*list(shadow._tasks))
        await asyncio.sleep(0)
    asyncio.run(scenario())
    assert len(calls) == 1
    receipt = saved["run"]
    assert receipt["status"] == "classified" and receipt["recommendation"] == "luna"
    assert receipt["applied"] is False and receipt["usage"]["cost"] == .0001
    assert len(receipt["handoff_sha256"]) == 64


@pytest.mark.parametrize("failure", ["timeout", "malformed", "claim", "finish"])
def test_failures_do_not_escape_or_leak_provider_bodies(monkeypatch, failure):
    saved = install_storage(monkeypatch)
    async def request(body, key):
        if failure == "timeout": await asyncio.sleep(1)
        if failure == "malformed": return {"usage": {"cost": .0001}, "answers": {"secret": "private"}}
        return answer()
    def unavailable(*args): raise RuntimeError("private database details")
    monkeypatch.setattr(shadow, "request", request)
    monkeypatch.setattr(shadow, "DEADLINE", .01)
    if failure in {"claim", "finish"}: monkeypatch.setattr(shadow, "_" + failure, unavailable)
    asyncio.run(shadow.observe(None, run(), shadow.packet("task", {}, 0), "hash"))
    assert "private" not in json.dumps(saved)
    if failure in {"timeout", "malformed"}:
        assert saved["run"]["status"] == "unavailable"
        assert saved["run"]["recommendation"] == "keep_current"
    if failure == "malformed": assert saved["run"]["usage"]["cost"] == .0001


def test_followups_and_missing_key_have_no_inference(monkeypatch):
    saved = install_storage(monkeypatch)
    async def forbidden(*args): raise AssertionError("must not call provider")
    monkeypatch.setattr(shadow, "request", forbidden)
    followup = {**run(), "round_no": 2}
    asyncio.run(shadow.observe(None, followup, shadow.packet("continue", {}, 0), "hash"))
    assert saved["run"]["reason"] == "continuation_affinity"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    asyncio.run(shadow.observe(None, {**run(), "id": "second"}, shadow.packet("task", {}, 0), "hash"))
    assert saved["second"]["reason"] == "missing_provider_key"


@pytest.mark.parametrize("gate", ["off", "select", "owner", "session", "bound", "observed", "capacity"])
def test_disabled_scoped_existing_and_overload_paths_skip_scheduling(monkeypatch, gate):
    r = run()
    if gate in {"off", "select"}: monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW", gate)
    if gate == "owner": monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_OWNERS", "another@test")
    if gate == "session": monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_SESSIONS", "another")
    if gate == "bound": r["hermes_run_id"] = "existing"
    if gate == "observed": r["context"][shadow.KEY] = {"status": "pending"}
    if gate == "capacity": monkeypatch.setattr(shadow, "MAX_PENDING", 0)
    def forbidden(*args): raise AssertionError("no packet construction")
    monkeypatch.setattr(shadow, "packet", forbidden)
    shadow.schedule(None, r, {}, "message")
    assert not shadow._tasks
