"""Deliberate tool outcomes are typed; only surprises are ``EXCEPTION``.

Observed over the live MCP: a policy ``PermissionError`` ("Specialists may be
delegated only from an active Calliope chat turn") and an AS OF request on a
current-only relation both came back as ``EXCEPTION`` with the hint
"unexpected server-side failure in this one tool" — precisely the wrong
signal for an agent deciding whether to retry. ``_tool_error_envelope`` maps
the deliberate cases to codes and hints an agent can act on and leaves the
generic fallback for genuinely unexpected failures.
"""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import sys
from pathlib import Path

import psycopg
import pytest
from mcp.server.fastmcp import FastMCP
from psycopg import errors as pgerr


_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import server  # noqa: E402


def _envelope(exc):
    return server._tool_error_envelope(exc)


def test_policy_denials_are_forbidden_not_failures():
    generic = _envelope(PermissionError("Only the artifact owner may update it"))
    assert generic["code"] == "FORBIDDEN"
    assert generic["message"].startswith("PermissionError: Only the artifact owner")
    assert "policy result" in generic["hint"]
    assert "unexpected" not in generic["hint"]

    turn_bound = _envelope(
        PermissionError("Specialists may be delegated only from an active Calliope chat turn")
    )
    assert turn_bound["code"] == "FORBIDDEN"
    assert "Calliope chat turn" in turn_bound["hint"]
    assert "direct MCP connector" in turn_bound["hint"]


def test_as_of_on_a_current_only_relation_is_typed():
    exc = pgerr.InternalError_(
        "rvbbit AS OF: table OID 29939 uses history_policy=current; historical reads are unavailable"
    )
    env = _envelope(exc)
    assert env["code"] == "AS_OF_UNAVAILABLE"
    assert "drop as_of" in env["hint"]
    assert "history_policy" in env["hint"]


def test_sql_mistakes_and_guards_get_actionable_codes():
    assert _envelope(pgerr.UndefinedColumn('column "at" does not exist'))["code"] == "SQL_ERROR"
    assert "describe_table" in _envelope(pgerr.UndefinedColumn("x"))["hint"]
    assert "search_data" in _envelope(pgerr.UndefinedTable("relation nope"))["hint"]
    assert _envelope(pgerr.SyntaxError("syntax error at or near"))["code"] == "SQL_ERROR"
    assert "nullif" in _envelope(pgerr.DivisionByZero("division by zero"))["hint"]

    ro = _envelope(pgerr.ReadOnlySqlTransaction("cannot execute UPDATE in a read-only transaction"))
    assert ro["code"] == "READ_ONLY"
    assert "administer_local_sql" in ro["hint"]

    assert _envelope(pgerr.InsufficientPrivilege("permission denied"))["code"] == "FORBIDDEN"
    assert _envelope(pgerr.QueryCanceled("canceling statement due to statement timeout"))["code"] == "TIMEOUT"
    assert _envelope(psycopg.OperationalError("connection refused"))["code"] == "DATABASE_UNAVAILABLE"
    assert _envelope(TimeoutError("hermes"))["code"] == "TIMEOUT"


def test_genuine_surprises_keep_the_generic_envelope():
    env = _envelope(KeyError("dimensions"))
    assert env["code"] == "EXCEPTION"
    assert env["message"] == "KeyError: 'dimensions'"
    assert env["hint"] == server._GENERIC_TOOL_FAILURE_HINT


_PERCENTILE_ERROR = "function approx_percentile(double precision, numeric) does not exist"
_ROUND_ERROR = "function round(double precision, integer) does not exist"
_DAY_ERROR = 'syntax error at or near "day"'


def test_percentile_dialect_hint_preserves_error_and_distinguishes_exactness(monkeypatch):
    class Connection:
        def execute(self, *_args):
            raise pgerr.UndefinedFunction(_PERCENTILE_ERROR)

    validation = server.tool_validate_sql("SELECT approx_percentile(value,0.5)", _connection=Connection())
    assert validation["valid"] is False
    assert validation["error"] == _PERCENTILE_ERROR
    assert "percentile_cont(0.5) WITHIN GROUP" in validation["hint"]
    assert "exact, not approximate" in validation["hint"]
    assert "capability_search" in validation["hint"]
    execution_error = _envelope(pgerr.UndefinedFunction(_PERCENTILE_ERROR))
    assert execution_error["code"] == "SQL_ERROR"
    assert execution_error["hint"] == validation["hint"]
    assert server._sql_recovery_hint("column approx_percentile does not exist") is None
    assert server._sql_recovery_hint("function other_approx_percentile(double precision) does not exist") is None


@pytest.mark.parametrize('message,exception,expected', [
    (_ROUND_ERROR, pgerr.UndefinedFunction, 'round(CAST(value AS numeric), 2)'),
    (_DAY_ERROR, pgerr.SyntaxError, 'AS "day"'),
])
def test_round_and_alias_hints_in_validation_and_execution(message, exception, expected):
    class Connection:
        def execute(self, *_args):
            raise exception(message)

    validation = server.tool_validate_sql('SELECT ...', _connection=Connection())
    execution = _envelope(exception(message))
    assert validation['error'] == message
    assert execution['code'] == 'SQL_ERROR'
    assert execution['message'] == f'{exception.__name__}: {message}'
    assert expected in validation['hint']
    assert validation['hint'] == execution['hint']
    assert 'Retry only the failed named query' in execution['hint']
    for unrelated in ('function round(text, integer) does not exist',
                      'function other_round(double precision, integer) does not exist',
                      'column round does not exist', 'syntax error at or near "daylight"'):
        assert server._sql_recovery_hint(unrelated) is None


@pytest.mark.parametrize('message', [_PERCENTILE_ERROR, _ROUND_ERROR, _DAY_ERROR])
def test_failed_dialect_batch_keeps_good_results_and_hint_in_both_modes(monkeypatch, message):
    monkeypatch.setattr(server, "_conn", lambda **_kwargs: nullcontext(object()))
    monkeypatch.setattr(server, "_catalog_discovery_redirect", lambda *_args: None)
    monkeypatch.setattr(server, "tool_validate_sql", lambda *_args, **_kwargs: {
        "valid":False,"error":message})
    failed = server.tool_run_sql("select approx_percentile(value,0.5) from measurements")
    assert failed["error"]["code"] == "INVALID_SQL"
    assert failed["error"]["message"] == message
    assert "Retry only the failed named query" in failed["error"]["hint"]
    good = {"rows":[{"n":5035}],"columns":[{"name":"n"}],"row_count":1}
    calls = []
    def run(sql, *_args, **_kwargs):
        calls.append(sql)
        return failed if sql == "bad" else good
    monkeypatch.setattr(server,"tool_run_sql",run)
    for mode in ("full","summary"):
        result = server.tool_run_sql_multi({"counts":"good","median":"bad"},result_mode=mode)
        assert result["results"]["counts"]["row_count"] == 1
        assert result["results"]["median"] == failed
    assert calls.count("good") == calls.count("bad") == 2  # No automatic retry/rewrite.


def test_logged_wrappers_use_the_typed_envelope(monkeypatch):
    monkeypatch.setattr(server, "_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_application_authorization_context", lambda: object())

    def boom():
        raise PermissionError("Specialists may be delegated only from an active Calliope chat turn")

    sync_result = server._logged("probe", {}, boom)
    assert sync_result["error"]["code"] == "FORBIDDEN"

    async def aboom():
        raise pgerr.ReadOnlySqlTransaction("cannot execute UPDATE in a read-only transaction")

    async_result = asyncio.run(server._logged_async("probe", {}, aboom))
    assert async_result["error"]["code"] == "READ_ONLY"


def test_get_tool_help_accepts_the_natural_string_spellings(monkeypatch):
    monkeypatch.setattr(server, "_capability_visible_names", lambda kind, names, **_kw: set(names))
    monkeypatch.setattr(server, "_record", lambda *_args, **_kwargs: None)
    mcp = FastMCP("tool-help-strings")
    server._register(mcp)
    fn = mcp._tool_manager._tools["get_tool_help"].fn

    for spelling in (
        "run_sql,describe_table",
        "run_sql, describe_table",
        "run_sql describe_table",
        '["run_sql","describe_table"]',
        ["run_sql", "describe_table"],
    ):
        result = asyncio.run(fn(spelling))
        assert [tool["name"] for tool in result["tools"]] == ["run_sql", "describe_table"], spelling
        assert "missing" not in result

    single = asyncio.run(fn("describe_table"))
    assert [tool["name"] for tool in single["tools"]] == ["describe_table"]
    assert asyncio.run(fn("  "))["error"]["code"] == "BAD_NAMES"
