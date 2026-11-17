"""Real disposable PostgreSQL checks plus the actual specialist launch path."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy
import importlib.util
import json
from pathlib import Path
from uuid import uuid4

import pytest
import calliope
import server
import specialist_model_shadow as shadow
from test_specialist_followups import db  # Existing isolated migration-backed fixture.
from test_specialist_model_shadow import answer

spec = importlib.util.spec_from_file_location("shadow_report", Path(__file__).parents[2] / "scripts/experiments/specialist_model_shadow_report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_atomic_claim_preserves_other_context_and_late_finish_is_allowed(db):
    r = {"id": db.rid}
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET context=%s::jsonb WHERE id=%s",
                  (json.dumps({"provided": {"important": "keep"}}), db.rid))
    receipts = [{"observation_id": str(i), "status": "pending"} for i in range(2)]
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda receipt: shadow._claim(db.factory, r, receipt), receipts))
    assert sum(claimed) == 1
    winner = receipts[claimed.index(True)]
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='complete',runner_id=NULL WHERE id=%s", (db.rid,))
    shadow._finish(db.factory, r, {**winner, "status": "classified"})
    shadow._finish(db.factory, r, {"observation_id": "wrong", "status": "overwrite"})
    with db.factory() as c:
        context = c.execute("SELECT context FROM rvbbit.calliope_specialist_runs WHERE id=%s", (db.rid,)).fetchone()["context"]
    assert context["provided"] == {"important": "keep"}
    assert context[shadow.KEY] == {**winner, "status": "classified"}


@pytest.mark.parametrize("provider_fails", [False, True])
def test_real_launcher_input_is_identical_and_completes_before_shadow_provider(db, monkeypatch, provider_fails):
    monkeypatch.setenv("CALLIOPE_STAGE_CONTEXT_MODE", "off")
    monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_OWNERS", "")
    monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW_SESSIONS", "")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    messages = []
    context = {"provided": {"queries_validated": True, "sql": "SELECT 1"}}
    monkeypatch.setattr(server, "_specialist_turn_image_parts", lambda *args: [])
    monkeypatch.setattr(calliope, "_session_sketch_image_part", lambda *args, **kwargs: None)
    monkeypatch.setattr(calliope, "project_messages", lambda *args: [])
    monkeypatch.setattr(calliope, "_publish_local_files", lambda *args: [])
    monkeypatch.setattr(server, "_queue_specialist_round_outcome", lambda rid: None)
    monkeypatch.setattr(server, "_specialist_result_payload", lambda rid: {"status": "complete"})
    async def hermes_json(*args, **kwargs): return {}
    monkeypatch.setattr(calliope, "_hermes_json", hermes_json)
    async def stream(client, config, conn, turn, session, **kwargs):
        assert session == "child_session"
        messages.append(copy.deepcopy(kwargs["message"]))
        kwargs["bind_run"]("hermes-run", 0)
        yield "assistant.completed", {"content": "Built the requested chart"}
        yield "run.completed", {"messages": [], "usage": {"input_tokens": 100}, "runtime": {"model": "sol"}}
    monkeypatch.setattr(calliope, "_iter_hermes_run", stream)

    async def scenario():
        release = asyncio.Event()
        async def request(*args):
            await release.wait()
            if provider_fails: raise RuntimeError("private upstream error")
            return answer()
        monkeypatch.setattr(shadow, "request", request)
        for mode in ["off", "shadow"]:
            monkeypatch.setenv("CALLIOPE_SPECIALIST_MODEL_SHADOW", mode)
            with db.factory() as c:
                c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='running',runner_id='worker',hermes_run_id=NULL,context=%s::jsonb WHERE id=%s",
                          (json.dumps(context), db.rid))
            result = await server._execute_specialist_round(db.rid, "worker")
            assert result["status"] == "complete" and not release.is_set()
        release.set()
        await asyncio.gather(*list(shadow._tasks))
        await asyncio.sleep(0)
        # Recovery/relaunch with the persisted audit must not put it into the
        # specialist's prompt or cause another selector invocation.
        with db.factory() as c:
            c.execute("UPDATE rvbbit.calliope_specialist_runs SET status='running',runner_id='worker',hermes_run_id=NULL WHERE id=%s", (db.rid,))
        assert (await server._execute_specialist_round(db.rid, "worker"))["status"] == "complete"
        assert not shadow._tasks
    asyncio.run(scenario())
    assert messages[0] == messages[1] == messages[2]
    assert shadow.KEY not in messages[1]
    with db.factory() as c:
        row = c.execute("SELECT context,status,runtime FROM rvbbit.calliope_specialist_runs WHERE id=%s", (db.rid,)).fetchone()
    assert row["status"] == "complete" and row["runtime"]["model"] == "sol"
    receipt = row["context"][shadow.KEY]
    assert receipt["status"] == ("unavailable" if provider_fails else "classified")
    assert not receipt["applied"] and row["context"]["provided"] == context["provided"]


def test_report_joins_actual_work_keeps_unknown_usage_and_enforces_owner_filter(db):
    receipt = {"status": "classified", "recommendation": "luna", "usage": {"cost": .0001}}
    with db.factory() as c:
        c.execute("UPDATE rvbbit.calliope_specialist_runs SET context=%s::jsonb,status='complete',started_at=now()-interval '4 minutes',completed_at=now(),runtime=%s::jsonb WHERE id=%s",
                  (json.dumps({shadow.KEY: receipt}), json.dumps({"model": "sol", "provider": "test"}), db.rid))
        c.execute("INSERT INTO rvbbit.calliope_specialist_messages(run_id,ordinal,role,kind,payload) VALUES(%s,1,'tool','tool_failed',%s::jsonb)",
                  (db.rid, json.dumps({"tool_name": "run_sql"})))
        c.execute("""INSERT INTO rvbbit.calliope_specialist_runs
            (id,specialist_id,owner_email,source_session_id,source_turn_id,hermes_session_id,status,task,thread_id,round_no)
            VALUES(%s,%s,'owner@example.com',%s,%s,'next','complete','Check the render',%s,2)""",
                  (str(uuid4()), db.sp, db.sid, db.tid, db.thread))
    data = report.collect(db.factory, "owner@example.com", session=db.sid)
    assert len(data["runs"]) == 2
    first = next(r for r in data["runs"] if r["round_no"] == 1)
    assert first["actual_model"] == "sol" and first["elapsed_seconds"] == 240
    assert first["usage"] == {}  # No invented cost or cache savings.
    assert first["later_rounds"] == 1 and first["tools"][0]["failed"] == 1
    assert data["summary"]["initial_candidate_lanes"] == {"luna": 1}
    assert data["summary"]["review_candidates"][0]["flags"] == ["has_followup_rounds", "tool_failures"]
    assert "luna" in report.markdown(data) and "sol" in report.markdown(data)
    assert not report.collect(db.factory, "other@example.com")["runs"]
    limited = report.collect(db.factory, "owner@example.com", limit=1)
    assert limited["limited"] and limited["runs"][0]["round_no"] == 2
