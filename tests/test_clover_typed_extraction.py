"""Typed-field extraction through the Clover gateway's `extractions` route.

clover_extract_fields and its typed wrappers read numbers, dates, spans and
lists in one pass, NULL where the text doesn't say; clover_extract_json tries
that typed route first for a contract whose fields it can type. These tests
stand a small gateway double in for Hutch: it answers every field from
`name=value` pairs in the text, so no provider key is needed.
"""
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class _Gateway(BaseHTTPRequestHandler):
    """POST /b/extractions/predict: one answer object per input."""

    requests = []

    def do_POST(self):  # noqa: N802 (http.server API)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Gateway.requests.append(body)
        outputs = [self._answer(item) for item in body["inputs"]]
        payload = json.dumps({"outputs": outputs}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    @staticmethod
    def _answer(item):
        fields = item["fields"]
        fields = json.loads(fields) if isinstance(fields, str) else fields
        facts = dict(part.strip().split("=", 1) for part in item["state"].split(";") if "=" in part)
        answers = {}
        for name, field in fields.items():
            raw = facts.get(name)
            if raw is None:
                answers[name] = {"type": field["type"], "value": None, "p_null": 0.99, "confidence": 0.9}
                continue
            if field["type"] == "number":
                value = int(raw) if field.get("integer") else float(raw)
            elif field["type"] == "list":
                value = [float(x) if field.get("items") == "number" else x for x in raw.split("|")]
            else:
                value = raw
            start = item["state"].find(raw)
            answers[name] = {"type": field["type"], "value": value, "p_null": 0.01,
                             "confidence": 0.97, "span": [start, start + len(raw)]}
        return {"model": "test/typed", "answers": answers, "truncated": False}

    def log_message(self, *args):
        pass


@pytest.fixture(scope="module")
def gateway():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/b/extractions/predict"
    server.shutdown()


@pytest.fixture
def typed(rvbbit, gateway):
    """Point the extractions backend at the double and mark the route served."""
    saved = rvbbit.execute(
        "SELECT endpoint_url, auth_header_env, transport_opts FROM rvbbit.backends WHERE name = 'extractions'"
    ).fetchone()
    rvbbit.execute(
        "SELECT rvbbit.register_backend('extractions', %s, 'rvbbit', 16, 4, 20000, NULL, "
        "'{\"clover_route\": {\"id\": \"extractions\", \"enabled\": true}}'::jsonb)",
        (gateway,),
    )
    rvbbit.execute("SELECT rvbbit.reload_backends()")
    _Gateway.requests.clear()
    yield
    if saved is None:
        rvbbit.execute("DELETE FROM rvbbit.backends WHERE name = 'extractions'")
    else:
        rvbbit.execute(
            "UPDATE rvbbit.backends SET endpoint_url = %s, auth_header_env = %s, transport_opts = %s "
            "WHERE name = 'extractions'",
            (saved[0], saved[1], json.dumps(saved[2])),
        )
    rvbbit.execute("SELECT rvbbit.reload_backends()")


def _unique(text):
    # Results are cached by input: a fresh marker keeps runs independent.
    return f"{text}; run={uuid.uuid4().hex}"


INVOICE = {
    "type": "object",
    "required": ["invoice_id", "total", "vendor"],
    "properties": {
        "invoice_id": {"type": "string", "description": "the invoice number"},
        "total": {"type": "number"},
        "lines": {"type": "integer"},
        "issued": {"type": ["string", "null"], "format": "date"},
        "vendor": {
            "type": "object",
            "required": ["name"],
            "properties": {"name": {"type": "string"}, "tax id": {"type": ["string", "null"]}},
        },
    },
}


def _extract(rvbbit, text, contract, policy=None):
    return rvbbit.execute(
        "SELECT rvbbit.clover_extract_json(%s, %s::jsonb, %s::jsonb)",
        (text, json.dumps(contract), json.dumps(policy or {})),
    ).fetchone()[0]


def test_a_contract_of_typable_fields_is_answered_by_the_typed_route(rvbbit, typed):
    text = _unique("invoice_id=INV-2291; total=1240.5; lines=3; vendor.name=Acme Corp")
    result = _extract(rvbbit, text, INVOICE)
    assert result["status"] == "ok", result
    assert result["data"] == {"invoice_id": "INV-2291", "total": 1240.5, "lines": 3,
                              "vendor": {"name": "Acme Corp"}}
    assert result["provenance"]["route"] == "typed"
    assert result["provenance"]["model_calls"] == 1
    evidence = result["evidence"]
    assert evidence["invoice_id"]["confidence"] == 0.97
    start, end = evidence["invoice_id"]["span"]
    assert text[start:end] == "INV-2291"
    assert evidence["issued"]["p_null"] == 0.99  # left out of the data: optional and unknown

    sent = _Gateway.requests[-1]["inputs"][0]["fields"]
    sent = json.loads(sent) if isinstance(sent, str) else sent
    assert set(sent) == {"invoice_id", "total", "lines", "issued", "vendor.name", "vendor.tax_id"}
    assert sent["lines"] == {"type": "number", "integer": True}
    assert sent["issued"]["type"] == "date"
    assert sent["invoice_id"]["instructions"] == "the invoice number"

    again = _extract(rvbbit, text, INVOICE)
    assert again["provenance"]["cache_hit"] is True and again["data"] == result["data"]


def test_a_typed_answer_the_contract_rejects_is_not_returned(rvbbit, typed):
    # vendor.name is required and the text doesn't give it.
    result = _extract(rvbbit, _unique("invoice_id=INV-1; total=5"), INVOICE,
                      {"fallback_on_rejection": False, "include_rejected": True})
    assert result["status"] == "rejected"
    assert result["data"] is None
    assert result["provenance"]["route"] == "typed"
    assert any(e["path"] == "$.vendor.name" for e in result["validation"]["errors"])
    assert result["rejected_data"]["typed"]["vendor"] == {"name": None}


def test_policy_reference_date_and_null_threshold_reach_the_extractor(rvbbit, typed):
    _extract(rvbbit, _unique("invoice_id=INV-7; total=1; vendor.name=A"), INVOICE,
             {"reference_date": "2026-11-07", "null_threshold": 0.8})
    request = _Gateway.requests[-1]["inputs"][0]
    fields = json.loads(request["fields"]) if isinstance(request["fields"], str) else request["fields"]
    assert request["reference_date"] == "2026-11-07"
    assert {f["null_threshold"] for f in fields.values()} == {0.8}


@pytest.mark.parametrize(
    "contract",
    (
        {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        {"type": "object", "properties": {"kind": {"type": "string", "enum": ["a", "b"]}}},
        {"type": "object", "properties": {"items": {"type": "array", "items": {
            "type": "object", "properties": {"x": {"type": "string"}}}}}},
        {"type": "object", "properties": {"v": {"type": ["string", "number"]}}},
    ),
    ids=("boolean", "enum", "array_of_objects", "mixed_types"),
)
def test_contracts_the_extractor_cannot_type_are_not_sent_to_it(rvbbit, typed, contract):
    result = _extract(rvbbit, "x", contract, {"route": "typed"})
    assert result["status"] == "error"
    assert result["validation"]["errors"][0]["code"] == "typed_unavailable"
    assert not _Gateway.requests


def test_a_gateway_that_does_not_serve_the_route_is_not_called(rvbbit, typed):
    rvbbit.execute(
        "UPDATE rvbbit.backends SET transport_opts = jsonb_set(transport_opts, '{clover_route,enabled}', 'false') "
        "WHERE name = 'extractions'"
    )
    assert rvbbit.execute("SELECT rvbbit._clover_extract_typed_ready()").fetchone()[0] is False
    result = _extract(rvbbit, "invoice_id=INV-1", INVOICE, {"route": "typed"})
    assert result["validation"]["errors"][0]["code"] == "typed_unavailable"
    assert "does not serve" in result["validation"]["errors"][0]["message"]
    assert not _Gateway.requests


def test_typed_wrappers_return_one_value_or_null(rvbbit, typed):
    text = _unique("refund_amount=42.1; approval_date=2026-11-02; order_number=7781; "
                   "outage_length=150; affected_regions=eu-west-1|us-east-2")
    value = lambda sql: rvbbit.execute(sql, (text,)).fetchone()[0]  # noqa: E731
    assert float(value("SELECT rvbbit.clover_extract_number(%s, 'refund amount', 'USD')")) == 42.1
    assert str(value("SELECT rvbbit.clover_extract_date(%s, 'approval date')")) == "2026-11-02"
    assert value("SELECT rvbbit.clover_extract_text(%s, 'order number')") == "7781"
    assert value("SELECT rvbbit.clover_extract_text(%s, 'customer email')") is None
    assert float(value("SELECT rvbbit.clover_extract_duration(%s, 'outage length', 'minutes')")) == 150
    assert value("SELECT rvbbit.clover_extract_list(%s, 'affected regions')") == ["eu-west-1", "us-east-2"]
    assert rvbbit.execute("SELECT rvbbit.clover_extract_number('', 'refund amount')").fetchone()[0] is None


def test_policy_values_are_checked(rvbbit):
    errors = rvbbit.execute(
        "SELECT rvbbit._clover_extract_policy_errors(%s::jsonb)",
        (json.dumps({"route": "fast", "reference_date": "Nov 7", "null_threshold": 1.5}),),
    ).fetchone()[0]
    assert {e["path"] for e in errors} == {"$.policy.route", "$.policy.reference_date", "$.policy.null_threshold"}
    assert rvbbit.execute(
        "SELECT rvbbit._clover_extract_policy_errors('{\"route\":\"typed\",\"reference_date\":\"2026-11-07\",\"null_threshold\":0.5}')"
    ).fetchone()[0] == []


class _Rollback(Exception):
    pass


def test_the_shipped_clover_install_runs(rvbbit):
    """Every statement of the catalog's managed/clover install applies (then rolls back)."""
    steps = rvbbit.execute(
        "SELECT jsonb_array_elements_text(manifest #> '{managed,install,sql}') "
        "FROM rvbbit.capability_catalog WHERE id = 'managed/clover' AND active"
    ).fetchall()
    assert steps
    with pytest.raises(_Rollback):
        with rvbbit.transaction():
            for (step,) in steps:
                rvbbit.execute(step)
            providers = {row[0] for row in rvbbit.execute(
                "SELECT DISTINCT source_provider FROM rvbbit.backends "
                "WHERE install_manifest->>'capability' = 'managed/clover'")}
            assert providers == {"self-hosted-clover"}
            raise _Rollback
