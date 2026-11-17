"""Regression cases from the Sonnet handoff/recovery loop; no LLM or database writes."""
import asyncio
import copy
import json
import runpy
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import agent_result_delivery as delivery
import server

RUN = "11111111-1111-4111-8111-111111111111"
THREAD = "22222222-2222-4222-8222-222222222222"
OWNER = "test@example.com"


def test_tiny_reply_does_not_resend_enormous_history_or_mutate_ui_snapshot():
    payload = {
        "run_id": RUN, "thread_id": THREAD, "status": "complete", "response": "recorded",
        "objective": "assignment " * 1000, "conversation": ["history" * 10000],
        "transcript": ["tools" * 20000], "stage_surfaces": [{"id": str(i)} for i in range(500)],
    }
    original = copy.deepcopy(payload)
    receipt = delivery.specialist_receipt(payload)
    assert len(json.dumps(receipt)) < 600
    assert receipt["response"] == "recorded"
    assert receipt["stage_surface_count"] == 500
    assert payload == original  # full Stage history remains available


@pytest.mark.parametrize("method", ["delegate", "continue"])
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_tool_boundaries_only_deliver_agent_receipts(monkeypatch, method, mode):
    async def run(*args, **kwargs):
        return {"run_id": RUN, "response": "done", "conversation": ["baggage" * 10000]}
    async def logged(name, args, fn):
        return await fn()
    monkeypatch.setattr(server, "_run_calliope_specialist", run)
    monkeypatch.setattr(server, "_logged_async", logged)
    fn = server._mcp_delegate_calliope_specialist if method == "delegate" else server._mcp_continue_calliope_specialist
    result = asyncio.run(fn("iris" if method == "delegate" else THREAD, "audit", mode=mode))
    assert result["response"] == "done" and "conversation" not in result


def test_fanout_pages_fit_as_one_tool_result_and_every_answer_is_recoverable():
    replies = [{"run_id": RUN, "response": "evidence " * 8000} for _ in range(16)]
    results = [delivery.specialist_receipt(p, response_chars=8000 // len(replies)) for p in replies]
    assert len(json.dumps({"results": results})) < 18000
    for result in results:
        assert result["response_chars"] == 72000
        assert result["read_more"]["arguments"] == {"run_id": RUN, "offset": 500}


class Connection:
    def __init__(self, rows):
        self.rows, self.calls = rows, []
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params):
        self.calls.append((sql, params))
        assert sql.startswith("SELECT ")
        return SimpleNamespace(fetchone=lambda: self.rows[0] if self.rows else None, fetchall=lambda: self.rows)


@pytest.fixture
def reader_context(monkeypatch):
    monkeypatch.setattr(server, "_require_application_subject", lambda: SimpleNamespace(subject=OWNER))
    monkeypatch.setattr(server, "_logged", lambda name, args, fn: fn())
    monkeypatch.setattr(server, "_hermes_mcp_envelope", lambda ctx=None: {"session_id": "calliope_specialist_session", "platform": "api_server"})
    monkeypatch.setattr(server, "_calliope_activity_for_hermes_session", lambda conn, ref: {
        "owner": OWNER, "calliope_session_id": THREAD,
    })
    def setup(rows):
        conn = Connection(rows)
        monkeypatch.setattr(server, "_conn", lambda: conn)
        return conn
    return setup


def test_specialist_reader_reassembles_exact_unicode_answer_with_owner_filter(reader_context):
    reply = "Verified αβγ\n" * 4000 + "FINAL DECISION"
    conn = reader_context([{"id": RUN, "thread_id": THREAD, "round_no": 3, "status": "complete", "result": reply}])
    first = delivery.specialist_receipt({"run_id": RUN, "response": reply})
    text, offset = first["response"], first["read_more"]["arguments"]["offset"]
    while offset is not None:
        page = server._mcp_read_calliope_specialist_result(RUN, offset, 1234)
        text += page["text"]
        offset = page["next_offset"]
    assert text == reply
    assert all("lower(owner_email)=lower(%s)" in sql and args == (RUN, OWNER) for sql, args in conn.calls)


def test_unavailable_specialist_result_is_not_returned(reader_context):
    reader_context([])
    with pytest.raises(PermissionError):
        server._mcp_read_calliope_specialist_result(RUN)


def test_sql_recall_recovers_exact_lost_case_and_applied_outcome(reader_context):
    sql = "CREATE TABLE calliope.events AS SELECT CASE WHEN seconds < 180 THEN 'short' ELSE 'long' END AS dur_bin;" * 100
    conn = reader_context([{"id": 17, "tool": "run_workspace_sql", "args": {"sql": sql}, "ok": True,
                            "result_summary": {"status": "complete"}}])
    output, offset = "", 0
    while offset is not None:
        page = server._mcp_recall_calliope_sql(receipt_id=17, offset=offset, limit=777)
        output += page["text"]
        offset = page["next_offset"]
    assert output == sql and page["outcome"]["status"] == "complete"
    for statement, args in conn.calls:
        assert "lower(subject)=lower(%s)" in statement
        assert "provenance->>'calliope_session_id'=%s" in statement
        assert args[:2] == [OWNER, THREAD] and args[-1] == 17


def test_batch_selection_and_failed_sql_are_not_reexecuted(reader_context):
    reader_context([{"id": 18, "tool": "run_sql_multi", "args": {"queries": {"a": "SELECT 1", "b": "SELECT missing"}},
                     "ok": False, "error": {"code": "SQL_ERROR"}}])
    index = server._mcp_recall_calliope_sql(receipt_id=18)
    assert index["query_names"] == ["a", "b"]
    page = server._mcp_recall_calliope_sql(receipt_id=18, query_name="b")
    assert page["text"] == "SELECT missing" and page["ok"] is False and page["error"]


def test_sql_search_has_cursor_and_bounded_previews(reader_context):
    rows = [{"id": i, "ts": "today", "tool": "run_sql_multi", "args": {"queries": {str(n): "SELECT " * 1000 for n in range(24)}}, "ok": True} for i in range(21, 10, -1)]
    conn = reader_context(rows)
    result = server._mcp_recall_calliope_sql(query="events_%", before_id=22)
    assert result["next_before_id"] == 12 and len(result["receipts"]) == 10
    assert len(json.dumps(result)) < 9000
    assert conn.calls[0][1][-2:] == ["events_%", 22]


def test_sql_recall_fails_closed_for_unbound_or_other_owner_context(reader_context, monkeypatch):
    conn = reader_context([])
    monkeypatch.setattr(server, "_calliope_activity_for_hermes_session", lambda *args: {"owner": "other@example.com", "calliope_session_id": THREAD})
    with pytest.raises(PermissionError):
        server._mcp_recall_calliope_sql(receipt_id=17)
    assert conn.calls == []
    monkeypatch.setattr(server, "_hermes_mcp_envelope", lambda: {})
    with pytest.raises(PermissionError):
        server._mcp_recall_calliope_sql()


@pytest.mark.parametrize("offset,limit", [(-1, 5), (0, 0), (0, 12001)])
def test_page_bounds(offset, limit):
    with pytest.raises(ValueError):
        delivery.text_page("saved", offset, limit)


def test_recovery_pins_upgrade_previous_default_but_preserve_operator_list(tmp_path):
    import yaml
    module = runpy.run_path(str(Path(__file__).resolve().parents[2] / "docker/calliope/reconcile-hermes-config.py"))
    pins = module["PINNED_TOOLS_DEFAULT"]
    recovery = module["_RESULT_RECOVERY_PINS"]
    previous = [p for p in pins if p not in recovery]
    for custom in [False, True]:
        configured = previous + (["mcp__Warehouse__operator_tool"] if custom else [])
        path = tmp_path / ("custom.yaml" if custom else "managed.yaml")
        model = {"default": "calliope-sonnet", "provider": "custom", "context_length": 262144, "max_tokens": 32768}
        path.write_text(yaml.safe_dump({"model": model, "agent": {"reasoning_effort": "xhigh"}, "tools": {"tool_search": {"pinned": configured}}}))
        module["reconcile"](path)
        actual = yaml.safe_load(path.read_text())
        assert actual["model"]["default"] == "calliope-sonnet"
        assert actual["agent"]["reasoning_effort"] == "xhigh"
        assert actual["tools"]["tool_search"]["pinned"] == (configured if custom else list(pins))


@pytest.mark.parametrize('encoding',['object','list','json'])
def test_sql_batch_encodings_preserve_names_sql_and_summary_mode(monkeypatch,encoding):
    batch={'first':'SELECT 1','second':'SELECT 2'}
    payload=batch if encoding=='object' else [{'name':k,'sql':v} for k,v in batch.items()]
    if encoding=='json':payload=json.dumps(payload)
    seen=[]
    monkeypatch.setattr(server,'_logged',lambda name,args,fn:fn())
    def run(queries,as_of,limit,mode,preview_rows,**kw):
        seen.append((queries,mode));return {'results':{}}
    monkeypatch.setattr(server,'tool_run_sql_multi',run)
    assert server._mcp_run_sql_multi(payload,result_mode='preview')=={'results':{}}
    assert seen==[(batch,'summary')]
    assert delivery.sql_statements({'queries':payload})==batch


@pytest.mark.parametrize('payload',[
    [{'name':'same','sql':'SELECT 1'},{'name':'same','sql':'SELECT 2'}],
    [{'name':'a','sql':'SELECT 1','ignored':'no'}], {'a':None}, [], 'invalid json',
])
def test_invalid_sql_batch_does_not_execute_partial_work(monkeypatch,payload):
    monkeypatch.setattr(server,'_logged',lambda name,args,fn:fn())
    monkeypatch.setattr(server,'tool_run_sql_multi',lambda *args,**kw:pytest.fail('Invalid batch was executed'))
    assert server._mcp_run_sql_multi(payload)['error']['code']=='BAD_QUERIES'


def test_batch_schema_declares_named_fields_for_closed_provider_objects():
    from mcp.server.fastmcp.tools import Tool
    tool=Tool.from_function(server._mcp_run_sql_multi)
    schema=server._inline_schema_defs(tool.parameters)
    variants=schema['properties']['queries']['anyOf']
    items=next(v['items'] for v in variants if v.get('type')=='array')
    item=schema['$defs'][items['$ref'].rsplit('/',1)[-1]] if '$ref' in items else items
    assert set(item['properties'])=={'name','sql'}
    assert set(item['required'])=={'name','sql'}
    # Closing additionalProperties must not erase the payload's named fields.
    import jsonschema
    jsonschema.validate({'name':'counts','sql':'SELECT count(*) FROM facts'}, {**item,'additionalProperties':False})
    with pytest.raises(jsonschema.ValidationError):jsonschema.validate({},item)
    args=tool.fn_metadata.arg_model.model_validate({'queries':[{'name':'counts','sql':'SELECT 1'}]})
    assert delivery.normalize_sql_batch(args.queries)=={'counts':'SELECT 1'}
    with pytest.raises(ValueError,match='Retry with'):
        delivery.normalize_sql_batch([{}])
