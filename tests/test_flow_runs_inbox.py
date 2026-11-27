"""Flow-run grouping, cache-hit breadcrumbs, direct-MCP receipts, and the
rvbbit.inbox queue (migration 0511).

Deterministic: semantic calls use a pure-code operator (no provider), and the
MCP tests use the 'rvbbit-test' stdio server behind the MCP gateway (skipped
when no ready gateway is registered).
"""
import json
import select
import uuid
from contextlib import contextmanager

import psycopg
import pytest

from conftest import RVBBIT_DSN


# ---- helpers -----------------------------------------------------------------


@pytest.fixture
def code_op(rvbbit):
    """A deterministic code-only operator: uppercases its input."""
    name = f"flow_probe_{uuid.uuid4().hex[:8]}"
    rvbbit.execute(
        "SELECT rvbbit.create_operator("
        "  op_name => %s, op_shape => 'scalar', "
        "  op_arg_names => ARRAY['text'], op_return_type => 'text', "
        "  op_system => 'unused', op_user => 'unused', "
        "  op_steps => %s::jsonb)",
        (
            name,
            '[{"name": "x", "kind": "code", "fn": "uppercase",'
            '  "inputs": {"text": "{{ inputs.text }}"}}]',
        ),
    )
    yield name
    rvbbit.execute("DELETE FROM rvbbit.operators WHERE name = %s", (name,))
    rvbbit.execute(f"DROP FUNCTION IF EXISTS rvbbit.{name}(text, jsonb)")


def scalar(conn, sql, params=None):
    return conn.execute(sql, params).fetchone()[0]


@contextmanager
def connect():
    with psycopg.connect(RVBBIT_DSN, autocommit=True) as c:
        yield c


# ---- 1. run / flow grouping --------------------------------------------------


def test_schema_present(rvbbit):
    cols = {
        (t, c)
        for t, c in rvbbit.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'rvbbit' AND column_name IN ('run_id','flow_id','receipt_id') "
            "  AND table_name IN ('receipts','cost_events','mcp_invocations','receipt_hits')"
        ).fetchall()
    }
    for t in ("receipts", "cost_events", "mcp_invocations", "receipt_hits"):
        assert (t, "run_id") in cols and (t, "flow_id") in cols, t
    assert ("mcp_invocations", "receipt_id") in cols
    # SELECT * views were re-expanded with the new columns.
    latest = {
        r[0]
        for r in rvbbit.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='rvbbit' AND table_name='cost_latest'"
        ).fetchall()
    }
    assert {"run_id", "flow_id", "caller"} <= latest


def test_begin_run_groups_receipts(rvbbit, code_op):
    rid = scalar(rvbbit, "SELECT rvbbit.begin_run('invoice_triage')")
    try:
        assert scalar(rvbbit, "SELECT rvbbit.current_run_id()") == rid
        assert scalar(rvbbit, "SELECT rvbbit.current_flow_id()") == "invoice_triage"
        marker = f"run-{uuid.uuid4().hex}"
        assert scalar(rvbbit, f"SELECT rvbbit.{code_op}(%s)", (marker,)) == marker.upper()
        row = rvbbit.execute(
            "SELECT run_id, flow_id FROM rvbbit.receipts WHERE operator = %s", (code_op,)
        ).fetchone()
        assert row == (rid, "invoice_triage")
        fr = rvbbit.execute(
            "SELECT flow_id, status FROM rvbbit.flow_runs WHERE run_id = %s", (rid,)
        ).fetchone()
        assert fr == ("invoice_triage", "running")
    finally:
        assert scalar(rvbbit, "SELECT rvbbit.end_run()") == rid
    assert scalar(rvbbit, "SELECT rvbbit.current_run_id()") is None
    assert scalar(rvbbit, "SELECT status FROM rvbbit.flow_runs WHERE run_id = %s", (rid,)) == "done"

    # Outside a run, receipts carry no run.
    marker = f"norun-{uuid.uuid4().hex}"
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
    assert (
        scalar(
            rvbbit,
            "SELECT run_id FROM rvbbit.receipts WHERE operator = %s AND inputs->>'text' = %s",
            (code_op, marker),
        )
        is None
    )


def test_txn_local_run_does_not_leak(rvbbit, code_op):
    with rvbbit.transaction():
        rid = scalar(rvbbit, "SELECT rvbbit.begin_run('t', txn_local => true)")
        rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (f"tx-{uuid.uuid4().hex}",))
        assert scalar(rvbbit, "SELECT rvbbit.current_run_id()") == rid
    assert scalar(rvbbit, "SELECT rvbbit.current_run_id()") is None
    assert scalar(rvbbit, "SELECT count(*) FROM rvbbit.receipts WHERE run_id = %s", (rid,)) == 1


def test_malformed_run_setting_never_breaks_writes(rvbbit, code_op):
    rvbbit.execute("SELECT set_config('rvbbit.run_id', 'not-a-uuid', false)")
    try:
        marker = f"bad-{uuid.uuid4().hex}"
        assert scalar(rvbbit, f"SELECT rvbbit.{code_op}(%s)", (marker,)) == marker.upper()
        assert (
            scalar(
                rvbbit,
                "SELECT count(*) FROM rvbbit.receipts WHERE operator = %s AND run_id IS NULL",
                (code_op,),
            )
            == 1
        )
    finally:
        rvbbit.execute("SELECT set_config('rvbbit.run_id', '', false)")


def test_cost_events_take_the_receipts_run(rvbbit):
    """Settlement appends from other sessions keep the priced call's run."""
    rid = uuid.uuid4()
    receipt = scalar(
        rvbbit,
        "INSERT INTO rvbbit.receipts (operator, inputs_hash, model, run_id, flow_id) "
        "VALUES ('manual_probe', '\\x00'::bytea, 'm', %s, 'f1') RETURNING receipt_id",
        (rid,),
    )
    try:
        # This session is in a different run: the receipt still wins.
        rvbbit.execute("SELECT rvbbit.begin_run('other')")
        req = scalar(
            rvbbit,
            "INSERT INTO rvbbit.cost_events (receipt_id, source, status, cost_source, cost_usd) "
            "VALUES (%s, 'manual', 'pending', 'test', NULL) RETURNING cost_request_id",
            (receipt,),
        )
        rvbbit.execute("SELECT rvbbit.end_run('abandoned')")
        # A later settle event for the same request with no receipt_id.
        rvbbit.execute(
            "INSERT INTO rvbbit.cost_events (cost_request_id, source, status, cost_source, cost_usd) "
            "VALUES (%s, 'manual', 'settled', 'test', 0.25)",
            (req,),
        )
        rows = rvbbit.execute(
            "SELECT run_id, flow_id FROM rvbbit.cost_events WHERE cost_request_id = %s", (req,)
        ).fetchall()
        assert rows == [(rid, "f1"), (rid, "f1")]
        latest = rvbbit.execute(
            "SELECT run_id, status FROM rvbbit.cost_latest WHERE cost_request_id = %s", (req,)
        ).fetchone()
        assert latest == (rid, "settled")
        summary = rvbbit.execute(
            "SELECT calls, cost_usd FROM rvbbit.run_summary WHERE run_id = %s", (rid,)
        ).fetchone()
        assert summary[0] == 1 and float(summary[1]) == pytest.approx(0.25)
    finally:
        rvbbit.execute("DELETE FROM rvbbit.cost_events WHERE receipt_id = %s OR run_id = %s", (receipt, rid))
        rvbbit.execute("DELETE FROM rvbbit.receipts WHERE receipt_id = %s", (receipt,))


def test_queued_receipts_keep_the_run_they_ran_in(rvbbit, code_op):
    """A call that cannot INSERT its receipt (read-only transaction, parallel
    worker, skip_receipts) queues it on disk, and some other backend drains it
    later. The drained receipt must keep the run of the call, not pick up
    whatever run the draining session has active.

    Uses a read-only transaction to reach the queue: forcing a parallel worker
    (debug_parallel_query) can abort the worker in 4.2.10 as well
    (pre-existing, see the PR notes), which would take the cluster down."""
    marker = f"ro-{uuid.uuid4().hex}"
    with connect() as ro:
        rid = scalar(ro, "SELECT rvbbit.begin_run('queued_flow')")
        with ro.transaction():
            ro.execute("SET TRANSACTION READ ONLY")
            ro.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
        ro.execute("SELECT rvbbit.end_run()")

    with connect() as other:
        other_run = scalar(other, "SELECT rvbbit.begin_run('someone_else')")
        other.execute("SELECT rvbbit.flush_receipt_queue(10000)")
        other.execute("SELECT rvbbit.end_run()")
    row = rvbbit.execute(
        "SELECT run_id, flow_id FROM rvbbit.receipts WHERE operator = %s AND inputs->>'text' = %s",
        (code_op, marker),
    ).fetchone()
    assert row is not None
    assert row == (rid, "queued_flow"), (row, rid, other_run)


# ---- 2. cache-hit breadcrumbs ------------------------------------------------


def test_cache_hits_leave_breadcrumbs_inside_a_run(rvbbit, code_op):
    rvbbit.execute("SELECT rvbbit.flush_cache()")
    rid = scalar(rvbbit, "SELECT rvbbit.begin_run('crumbs')")
    try:
        marker = f"hit-{uuid.uuid4().hex}"
        for _ in range(2):  # miss, then L1 hit
            rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
        rvbbit.execute("SELECT rvbbit.flush_cache()")
        rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))  # L2 hit
    finally:
        rvbbit.execute("SELECT rvbbit.end_run()")

    # receipts unchanged: still exactly one real call (existing contract).
    receipt = rvbbit.execute(
        "SELECT receipt_id FROM rvbbit.receipts WHERE operator = %s", (code_op,)
    ).fetchall()
    assert len(receipt) == 1
    receipt = receipt[0][0]

    hits = rvbbit.execute(
        "SELECT cache_layer, source_receipt_id, flow_id FROM rvbbit.receipt_hits "
        "WHERE run_id = %s ORDER BY hit_id",
        (rid,),
    ).fetchall()
    assert [h[0] for h in hits] == ["l1", "l2"]
    assert hits[0][1] is None and hits[1][1] == receipt  # L2 knows its source
    assert all(h[2] == "crumbs" for h in hits)

    trail = rvbbit.execute(
        "SELECT kind, cache_layer, source_receipt_id, cost_usd FROM rvbbit.run_trail "
        "WHERE run_id = %s ORDER BY at",
        (rid,),
    ).fetchall()
    assert [t[0] for t in trail] == ["call", "cache_hit", "cache_hit"]
    assert all(t[2] == receipt for t in trail), trail  # L1 resolved via inputs_hash
    assert all(float(t[3] or 0) == 0 for t in trail[1:])

    s = rvbbit.execute(
        "SELECT flow_id, status, calls, cache_hits FROM rvbbit.run_summary WHERE run_id = %s",
        (rid,),
    ).fetchone()
    assert s == ("crumbs", "done", 1, 2)


def test_cache_hit_logging_modes(rvbbit, code_op):
    marker = f"mode-{uuid.uuid4().hex}"
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))

    def hits():
        return scalar(
            rvbbit, "SELECT count(*) FROM rvbbit.receipt_hits WHERE operator = %s", (code_op,)
        )

    # default 'run': no run active -> nothing logged
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
    assert hits() == 0
    # 'on': every hit
    rvbbit.execute("SET rvbbit.cache_hit_receipts = 'on'")
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
    assert hits() == 1
    # 'off': never, even inside a run
    rvbbit.execute("SET rvbbit.cache_hit_receipts = 'off'")
    rvbbit.execute("SELECT rvbbit.begin_run('quiet')")
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
    rvbbit.execute("SELECT rvbbit.end_run()")
    rvbbit.execute("RESET rvbbit.cache_hit_receipts")
    assert hits() == 1
    assert scalar(rvbbit, "SELECT count(*) FROM rvbbit.receipts WHERE operator = %s", (code_op,)) == 1


def test_cache_hits_in_read_only_transactions_are_skipped(rvbbit, code_op):
    marker = f"ro-{uuid.uuid4().hex}"
    rvbbit.execute(f"SELECT rvbbit.{code_op}(%s)", (marker,))
    rvbbit.execute("SELECT rvbbit.begin_run('ro')")
    try:
        with rvbbit.transaction():
            rvbbit.execute("SET TRANSACTION READ ONLY")
            assert scalar(rvbbit, f"SELECT rvbbit.{code_op}(%s)", (marker,)) == marker.upper()
    finally:
        rvbbit.execute("SELECT rvbbit.end_run()")
    assert scalar(rvbbit, "SELECT count(*) FROM rvbbit.receipt_hits WHERE operator = %s", (code_op,)) == 0


# ---- 3. direct MCP calls write receipts --------------------------------------


@pytest.fixture
def mcp_server(rvbbit):
    ready = rvbbit.execute(
        "SELECT 1 FROM rvbbit.mcp_gateways WHERE status = 'ready' LIMIT 1"
    ).fetchone()
    if ready is None:
        pytest.skip("no ready MCP gateway registered")
    name = f"mcpflow_{uuid.uuid4().hex[:6]}"
    rvbbit.execute(
        "SELECT rvbbit.register_mcp_server(server_name => %s, server_transport => 'stdio', "
        "  server_command => 'python', server_args => ARRAY['/opt/mcp-test-server/main.py'])",
        (name,),
    )
    assert scalar(rvbbit, "SELECT rvbbit.refresh_mcp_server(%s)", (name,)) >= 3
    yield name
    rvbbit.execute("SELECT rvbbit.drop_mcp_server(%s)", (name,))


def test_direct_mcp_call_writes_linked_receipt(rvbbit, mcp_server):
    rid = scalar(rvbbit, "SELECT rvbbit.begin_run('mcp_flow')")
    try:
        out = scalar(
            rvbbit,
            "SELECT rvbbit.mcp_text(rvbbit.mcp_call(%s, 'echo', '{\"text\":\"hi\"}'::jsonb))",
            (mcp_server,),
        )
        assert out == "hi"
    finally:
        rvbbit.execute("SELECT rvbbit.end_run()")

    inv = rvbbit.execute(
        "SELECT receipt_id, run_id, flow_id FROM rvbbit.mcp_invocations WHERE server = %s",
        (mcp_server,),
    ).fetchall()
    assert len(inv) == 1 and inv[0][0] is not None
    receipt_id = inv[0][0]
    assert inv[0][1:] == (rid, "mcp_flow")

    r = rvbbit.execute(
        "SELECT operator, model, run_id, flow_id, error, sub_calls->0->>'kind', "
        "       inputs->>'tool', latency_ms IS NOT NULL "
        "FROM rvbbit.receipts WHERE receipt_id = %s",
        (receipt_id,),
    ).fetchone()
    assert r == ("mcp_call", f"{mcp_server}.echo", rid, "mcp_flow", None, "mcp", "echo", True)

    # Exactly one cost event for the call, linked to the receipt (the old
    # unlinked per-invocation event is not written in addition).
    ce = rvbbit.execute(
        "SELECT receipt_id, source, tool, run_id FROM rvbbit.cost_events "
        "WHERE tool = %s",
        (f"{mcp_server}.echo",),
    ).fetchall()
    assert ce == [(receipt_id, "mcp", f"{mcp_server}.echo", rid)]
    assert scalar(rvbbit, "SELECT audit_status FROM rvbbit.receipt_cost_audit WHERE receipt_id = %s", (receipt_id,)) in ("ok", "uncosted")  # no cost_policy for the test tool


def test_direct_mcp_errors_and_rows(rvbbit, mcp_server):
    rvbbit.execute("SELECT rvbbit.mcp_call(%s, 'failing', '{}'::jsonb)", (mcp_server,))
    err = rvbbit.execute(
        "SELECT r.error IS NOT NULL, r.output IS NULL FROM rvbbit.mcp_invocations i "
        "JOIN rvbbit.receipts r USING (receipt_id) WHERE i.server = %s AND i.tool = 'failing'",
        (mcp_server,),
    ).fetchone()
    assert err == (True, True)

    n = scalar(
        rvbbit,
        "SELECT count(*) FROM rvbbit.mcp_rows(%s, 'list_items', '{\"n\":3}'::jsonb)",
        (mcp_server,),
    )
    assert n == 3
    op = rvbbit.execute(
        "SELECT r.operator FROM rvbbit.mcp_invocations i JOIN rvbbit.receipts r USING (receipt_id) "
        "WHERE i.server = %s AND i.tool = 'list_items'",
        (mcp_server,),
    ).fetchone()
    assert op == ("mcp_rows",)


def test_direct_mcp_receipts_can_be_turned_off(rvbbit, mcp_server):
    rvbbit.execute("SET rvbbit.mcp_receipts = off")
    try:
        rvbbit.execute("SELECT rvbbit.mcp_call(%s, 'add', '{\"a\":1,\"b\":2}'::jsonb)", (mcp_server,))
    finally:
        rvbbit.execute("RESET rvbbit.mcp_receipts")
    assert scalar(rvbbit, "SELECT receipt_id FROM rvbbit.mcp_invocations WHERE server = %s", (mcp_server,)) is None
    assert scalar(rvbbit, "SELECT count(*) FROM rvbbit.receipts WHERE model = %s", (f"{mcp_server}.add",)) == 0
    # previous behaviour: one unlinked cost event
    assert scalar(
        rvbbit,
        "SELECT count(*) FROM rvbbit.cost_events WHERE tool = %s AND receipt_id IS NULL",
        (f"{mcp_server}.add",),
    ) == 1


def test_direct_mcp_cache_hit_breadcrumb(rvbbit, mcp_server):
    rvbbit.execute("SELECT rvbbit.set_mcp_tool_caching(%s, 'echo')", (mcp_server,))
    rid = scalar(rvbbit, "SELECT rvbbit.begin_run('mcp_cache')")
    try:
        for _ in range(2):
            rvbbit.execute("SELECT rvbbit.mcp_call(%s, 'echo', '{\"text\":\"c\"}'::jsonb)", (mcp_server,))
    finally:
        rvbbit.execute("SELECT rvbbit.end_run()")
    assert scalar(rvbbit, "SELECT count(*) FROM rvbbit.receipts WHERE run_id = %s", (rid,)) == 1
    trail = rvbbit.execute(
        "SELECT kind, operator, cache_layer, source_receipt_id IS NOT NULL FROM rvbbit.run_trail "
        "WHERE run_id = %s ORDER BY at",
        (rid,),
    ).fetchall()
    assert trail == [("call", "mcp_call", None, True), ("cache_hit", "mcp_call", "mcp", True)]


# ---- 4. inbox -----------------------------------------------------------------


@pytest.fixture
def source(rvbbit):
    name = f"test_{uuid.uuid4().hex[:8]}"
    yield name
    rvbbit.execute("DELETE FROM rvbbit.inbox WHERE source LIKE %s", (name + "%",))


def test_inbox_put_is_idempotent(rvbbit, source):
    a = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"n\":1}'::jsonb, 'k1')", (source,))
    b = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"n\":2}'::jsonb, 'k1')", (source,))
    assert a == b
    assert scalar(rvbbit, "SELECT payload->>'n' FROM rvbbit.inbox WHERE id = %s", (a,)) == "1"
    # same key, different source -> different item; no key -> always new
    other = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{}'::jsonb, 'k1')", (source + "_b",))
    assert other != a
    n1 = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{}'::jsonb)", (source,))
    n2 = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{}'::jsonb)", (source,))
    assert n1 != n2
    with pytest.raises(psycopg.errors.RaiseException):
        rvbbit.execute("SELECT rvbbit.inbox_put('  ', '{}'::jsonb)")


def test_inbox_notify_on_insert(rvbbit, source):
    with connect() as listener:
        listener.execute("LISTEN rvbbit_inbox")
        item = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"x\":1}'::jsonb, 'n1')", (source,))
        got = []
        for note in listener.notifies(timeout=5, stop_after=1):
            got.append(json.loads(note.payload))
        assert got and got[0]["id"] == item and got[0]["status"] == "new"
        assert got[0]["source"] == source


def test_inbox_claims_skip_locked(rvbbit, source):
    ids = [scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, %s::jsonb)", (source, json.dumps({"i": i}))) for i in range(4)]
    with connect() as a, connect() as b:
        with a.transaction(), b.transaction():
            got_a = [r[0] for r in a.execute(
                "SELECT id FROM rvbbit.inbox_claim('new', n => 2, worker => 'a', source => %s)", (source,)
            ).fetchall()]
            got_b = [r[0] for r in b.execute(
                "SELECT id FROM rvbbit.inbox_claim('new', n => 10, worker => 'b', source => %s)", (source,)
            ).fetchall()]
    assert len(got_a) == 2
    assert set(got_a).isdisjoint(got_b)
    assert set(got_a) | set(got_b) == set(ids)
    rows = rvbbit.execute(
        "SELECT status, attempts, run_id IS NOT NULL, lease_until > now() FROM rvbbit.inbox WHERE source = %s",
        (source,),
    ).fetchall()
    assert all(r == ("processing", 1, True, True) for r in rows)


def test_inbox_lifecycle_with_example_router(rvbbit, source):
    """System router -> user flows. The router here is a CASE expression; in
    production it is an rvbbit.decision_tree over the payload (see docs)."""
    items = {
        "invoice": scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"kind\":\"invoice\",\"total\":120}'::jsonb, 'inv-1')", (source,)),
        "ticket": scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"kind\":\"ticket\"}'::jsonb, 'tk-1')", (source,)),
        "junk": scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{\"kind\":\"???\"}'::jsonb, 'jk-1')", (source,)),
    }
    routed = rvbbit.execute(
        """
        WITH claimed AS (
            SELECT * FROM rvbbit.inbox_claim('new', n => 10, worker => 'router', source => %s)
        ), decided AS (
            SELECT id, CASE payload->>'kind'
                         WHEN 'invoice' THEN 'ap_triage'
                         WHEN 'ticket'  THEN 'support_routing' END AS flow
              FROM claimed
        )
        SELECT id,
               CASE WHEN flow IS NULL
                    THEN (rvbbit.inbox_needs_human(id, 'router: no confident route')).status
                    ELSE (rvbbit.inbox_route(id, flow, jsonb_build_object('router', 'example'))).status
               END
          FROM decided ORDER BY id
        """,
        (source,),
    ).fetchall()
    assert dict(routed) == {items["invoice"]: "routed", items["ticket"]: "routed", items["junk"]: "needs_human"}

    # The AP flow worker only sees its own items.
    claimed = rvbbit.execute(
        "SELECT id, run_id, routed_flow FROM rvbbit.inbox_claim('routed', flow => 'ap_triage', n => 10, source => %s)",
        (source,),
    ).fetchall()
    assert [c[0] for c in claimed] == [items["invoice"]]
    item_id, run_id, flow = claimed[0]
    assert scalar(rvbbit, "SELECT rvbbit.begin_run(%s, %s, inbox_id => %s)", (flow, run_id, item_id)) == run_id
    rvbbit.execute("SELECT rvbbit.end_run()")
    done = rvbbit.execute(
        "SELECT (rvbbit.inbox_complete(%s, '{\"action\":\"queued_for_approval\"}'::jsonb)).status", (item_id,)
    ).fetchone()[0]
    assert done == "done"
    assert scalar(rvbbit, "SELECT inbox_id FROM rvbbit.run_summary WHERE run_id = %s", (run_id,)) == item_id

    # A person resolves the parked item and sends it on.
    assert rvbbit.execute(
        "SELECT (rvbbit.inbox_requeue(%s, 'support_routing')).status", (items["junk"],)
    ).fetchone()[0] == "routed"

    stats = dict(
        ((f, s), n)
        for f, s, n in rvbbit.execute(
            "SELECT routed_flow, status, items FROM rvbbit.inbox_stats WHERE source = %s", (source,)
        ).fetchall()
    )
    assert stats == {("ap_triage", "done"): 1, ("support_routing", "routed"): 2}


def test_inbox_fail_retries_with_backoff_then_fails(rvbbit, source):
    item = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{}'::jsonb, 'r1', max_attempts => 2)", (source,))
    rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('new', n => 10, source => %s)", (source,))
    st, wait = rvbbit.execute(
        "SELECT status, available_at > clock_timestamp() + interval '50 seconds' "
        "FROM rvbbit.inbox_fail(%s, 'boom', backoff => interval '1 minute')",
        (item,),
    ).fetchone()
    assert (st, wait) == ("new", True)
    # Not claimable until the backoff passes.
    assert rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('new', n => 10, source => %s)", (source,)).fetchall() == []
    rvbbit.execute("UPDATE rvbbit.inbox SET available_at = now() - interval '1 second' WHERE id = %s", (item,))
    assert len(rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('new', n => 10, source => %s)", (source,)).fetchall()) == 1
    st, attempts, err = rvbbit.execute("SELECT status, attempts, error FROM rvbbit.inbox_fail(%s, 'boom again')", (item,)).fetchone()
    assert (st, attempts, err) == ("failed", 2, "boom again")
    with pytest.raises(psycopg.errors.RaiseException):
        rvbbit.execute("SELECT rvbbit.inbox_fail(%s, 'x')", (item,))


def test_inbox_expired_lease_is_reclaimed_at_the_same_stage(rvbbit, source):
    item = scalar(rvbbit, "SELECT rvbbit.inbox_put(%s, '{}'::jsonb, 'l1')", (source,))
    rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('new', n => 10, lease => interval '1 second', source => %s)", (source,))
    rvbbit.execute("SELECT rvbbit.inbox_route(%s, 'f')", (item,))
    rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('routed', flow => 'f', worker => 'dead', source => %s)", (source,))
    rvbbit.execute("UPDATE rvbbit.inbox SET lease_until = now() - interval '1 second' WHERE id = %s", (item,))
    # An expired routed-stage claim is not offered to the router...
    assert rvbbit.execute("SELECT * FROM rvbbit.inbox_claim('new', n => 10, source => %s) WHERE id = %s", (source, item)).fetchall() == []
    # ...but is offered to the flow again, as attempt 2.
    row = rvbbit.execute(
        "SELECT id, attempts, claimed_by FROM rvbbit.inbox_claim('routed', flow => 'f', worker => 'w2', source => %s) WHERE id = %s",
        (source, item),
    ).fetchone()
    assert row == (item, 2, "w2")
