"""Search reranking boundaries and integration, with no live provider calls."""
import asyncio
import json
from pathlib import Path
import sys

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evidence_ranking as ranking
import server


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("CALLIOPE_EVIDENCE_RANKING_MODE", "select")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    ranking._cache.clear()
    import calliope
    monkeypatch.setattr(calliope, "_match_term_bindings", lambda *_args, **_kwargs: [])


def item(index, group="knowledge", doc=None):
    return {"id": f"{group}:{index}", "group": group, "title": f"Candidate {index}",
            "kind": "document" if group == "knowledge" else "artifact",
            "handle": {"doc_id": doc if doc is not None else index}}


def response(choices):
    return {"answers": {f"c{i}": {"type": "choice", "choice": choice,
            "probabilities": {c: .97 if c == choice else .01 for c in ranking.CRITERIA}}
            for i, choice in enumerate(choices)}}


def test_batch_descriptor_is_bounded_and_excludes_rows_pixels_and_sql():
    candidate = {**item(1), "summary": "x" * 5000, "sql": "secret", "rows": ["secret"], "metric": {"series": ["secret"]}}
    body = ranking.payload("question", [candidate])
    assert len(ranking.descriptor(candidate)["summary"]) == 900
    assert "Candidate 1" in body["questions"]["c0"]["instructions"]
    assert "secret" not in json.dumps(body)
    assert len(body["questions"]) == 1


def test_lower_ranked_candidate_can_win_and_cache_is_acl_scoped(monkeypatch):
    calls = []
    async def decide(body, key):
        calls.append(body)
        return response(["uncertain"] * 9 + ["direct"])
    monkeypatch.setattr(ranking, "request", decide)
    pool = [item(i) for i in range(10)]
    ranked, receipt = ranking.rank("question", "a@example.com", pool, pool[:2])
    assert ranked[0]["id"] == "knowledge:9"
    assert receipt["status"] == "ranked"
    assert not receipt["cached"]
    assert ranking.rank("question", "a@example.com", pool, pool[:4])[1]["cached"]
    reordered, cached = ranking.rank("question", "a@example.com", list(reversed(pool)), pool[:2])
    assert cached["cached"] and reordered[0]["id"] == "knowledge:9"
    ranking.rank("question", "b@example.com", pool, pool[:2])
    changed = [dict(i) for i in pool]
    changed[0]["summary"] = "changed evidence"
    ranking.rank("question", "a@example.com", changed, pool[:2])
    assert len(calls) == 3


@pytest.mark.parametrize("failure", ["timeout", "missing", "nan", "invented"])
def test_errors_preserve_original_results_and_are_not_cached(monkeypatch, failure):
    async def decide(*_args):
        if failure == "timeout":
            raise TimeoutError()
        result = response(["direct", "supporting"])
        if failure == "missing":
            result["answers"].pop("c1")
        elif failure == "nan":
            result["answers"]["c0"]["probabilities"]["direct"] = float("nan")
        else:
            result["answers"]["c0"]["choice"] = "invented"
        return result
    monkeypatch.setattr(ranking, "request", decide)
    pool = [item(1), item(2)]
    result, receipt = ranking.rank("question", "owner", pool, pool[:1])
    assert result == pool[:1]
    assert receipt["status"] == "fallback"
    assert not ranking._cache


def test_shadow_and_disabled_do_not_change_results(monkeypatch):
    async def decide(*_args):
        return response(["unrelated", "direct"])
    monkeypatch.setattr(ranking, "request", decide)
    pool = [item(1), item(2)]
    monkeypatch.setenv("CALLIOPE_EVIDENCE_RANKING_MODE", "shadow")
    result, receipt = ranking.rank("question", "owner", pool, pool[:1])
    assert result == pool[:1] and receipt["status"] == "shadow"
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert not ranking.enabled()
    assert ranking.rank("question", "owner", pool, pool[:1])[1]["status"] == "skipped"


def test_chunk_diversity_does_not_promote_unrelated_sources():
    pool = [item(1, doc=1), item(2, doc=1), item(3, doc=2), item(4, doc=3)]
    decisions = [("direct", .99), ("direct", .98), ("direct", .97), ("unrelated", .99)]
    assert [i["id"] for i in ranking.order(pool, decisions)] == ["knowledge:1", "knowledge:3", "knowledge:2", "knowledge:4"]


def test_no_useful_evidence_keeps_balanced_baseline(monkeypatch):
    async def decide(*_a):
        return response(["unrelated", "uncertain"])
    monkeypatch.setattr(ranking, "request", decide)
    pool = [item(1), item(2)]
    result, receipt = ranking.rank("question", "owner", pool, pool[:1])
    assert result == pool[:1]
    assert receipt["status"] == "unchanged"
    assert receipt["reason"] == "no_relevant_candidates"


def test_cache_expiry_and_result_normalization(monkeypatch):
    calls = []
    async def decide(*_a):
        calls.append(True)
        return response(["direct", "supporting"])
    monkeypatch.setattr(ranking, "request", decide)
    pool = [item(1), item(2)]
    ranking.rank("question", "owner", pool, pool)
    monkeypatch.setattr(ranking, "CACHE_TTL", -1)
    items, receipt = ranking.rank("question", "owner", pool, pool)
    assert len(calls) == 2 and not receipt["cached"]
    import calliope
    normalized = calliope._normalize_evidence_search_result({"items": items, "ranking": receipt}, "question")
    assert normalized["ranking"]["status"] == "ranked"
    assert normalized["ranking"]["candidate_count"] == 2


def test_forty_candidates_before_limit_and_same_prefix_on_promotion(monkeypatch):
    calls = []
    for name, group in [("brain", "knowledge"), ("artifact", "artifacts"), ("data", "data"), ("metric", "metrics")]:
        def resolver(*args, group=group):
            calls.append((group, args))
            return [item(i, group) for i in range(10)]
        monkeypatch.setattr(server, f"_calliope_{name}_evidence", resolver)
    async def decide(body, _key):
        assert len(body["questions"]) == 40
        return response(["direct" if json.loads(q["instructions"].split("Candidate: ", 1)[1])["id"] == "knowledge:9"
                         else "supporting" for q in body["questions"].values()])
    monkeypatch.setattr(ranking, "request", decide)
    small = server._calliope_evidence_search("question", "owner", 8)
    large = server._calliope_evidence_search("question", "owner", 24)
    assert len(small["items"]) == 8 and len(large["items"]) == 24
    assert small["items"][0]["id"] == "knowledge:9"
    assert small["items"] == large["items"][:8]
    assert large["ranking"]["cached"]
    assert all(args[-2:] == (10, True) for group, args in calls if group == "data")
    # Explicit term bindings override even a negative model judgment.
    import calliope
    monkeypatch.setattr(calliope, "_match_term_bindings", lambda *_a, **_k: [
        {"status": "affirmed", "term_norm": "answer", "referent": {"kind": "artifact", "slug": "artifacts:8"}}])
    pinned = server._calliope_evidence_search("question", "owner", 8)
    assert pinned["items"][0]["id"] == "artifacts:8"
    assert pinned["items"][0]["bound_term"] == "answer"
    async def unavailable(*_a):
        raise TimeoutError()
    monkeypatch.setattr(ranking, "request", unavailable)
    ranking._cache.clear()
    selected = server._calliope_evidence_search("question", "owner", 8, protected_id="data:9")
    assert selected["ranking"]["status"] == "fallback"
    assert selected["items"][0]["id"] == "data:9"
    assert len(selected["items"]) == 8


def test_data_candidate_selection_can_prefer_relevance_over_kind(monkeypatch):
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *_a): pass
        def execute(self, *_a): return self
        def fetchall(self):
            return [{"node_id": i, "kind": kind, "schema_name": "sales", "rel_name": kind,
                     "boosted_score": score, "doc": kind} for i, kind, score in [(1, "cube", .2), (2, "db_table", .95)]]
    monkeypatch.setattr(server, "_conn", Connection)
    monkeypatch.setattr(server, "_calliope_data_supplements", lambda *_a: {})
    monkeypatch.setattr(server, "_calliope_data_presentation", lambda *_a: {})
    assert server._calliope_data_evidence("question", 1)[0]["kind"] == "cube"
    assert server._calliope_data_evidence("question", 1, True)[0]["kind"] == "db_table"


def test_provider_wait_has_total_deadline(monkeypatch):
    async def slow(_request):
        await asyncio.sleep(.2)
        return httpx.Response(200, json={})
    original = httpx.AsyncClient
    monkeypatch.setattr(ranking.httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(slow), **kwargs))
    monkeypatch.setattr(ranking, "DEADLINE", .01)
    with pytest.raises(TimeoutError):
        asyncio.run(ranking.request({}, "test"))
