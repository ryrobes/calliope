from __future__ import annotations

import base64
import csv
import io
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "docker" / "desktop"))
import calliope_proving_ground as lab  # noqa: E402


def _download(run_id: str) -> str:
    status, headers, body = lab.get(f"/lab/download?run={run_id}")
    assert status == 200
    assert headers["Content-Type"].startswith("text/csv")
    return body.decode()


def _download_bytes(run_id: str) -> tuple[dict[str, str], bytes]:
    status, headers, body = lab.get(f"/lab/download?run={run_id}")
    assert status == 200
    return headers, body


def _edit(source: str, **changes: str) -> str:
    rows = list(csv.DictReader(io.StringIO(source)))
    rows[0].update(changes)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def test_recoverable_trial_rejects_once_then_commits_exactly_once():
    run = lab.start("recoverable_combo")
    run_id = run["run_id"]
    page_status, _, page = lab.get(f"/lab?run={run_id}")
    assert page_status == 200
    assert b"Portal layout updated" in page
    assert b"If the status is not covered" in page

    source = _download(run_id)
    reviewed = _edit(source, owner_code="OPS-Z", review_note="reviewed")
    lab.post("/lab/acknowledge", {"run_id": run_id})
    with pytest.raises(lab.LabError) as transient:
        lab.post("/lab/upload", {"run_id": run_id, "content": reviewed})
    assert transient.value.code == "LAB_UPLOAD_TRANSIENT"
    assert transient.value.status == 503

    status, _, value = lab.post("/lab/upload", {"run_id": run_id, "content": reviewed})
    assert status == 200
    assert b'"ok":true' in value
    _, _, submitted = lab.post("/lab/submit", {"run_id": run_id})
    assert b'"duplicate":false' in submitted
    _, _, duplicate = lab.post("/lab/submit", {"run_id": run_id})
    assert b'"duplicate":true' in duplicate

    receipt = lab.receipt(run_id)
    assert receipt["state"] == "submitted"
    assert receipt["upload_attempts"] == 2
    assert receipt["duplicate_submits"] == 1
    assert receipt["submission_id"].startswith("SUB-")
    assert [item["event"] for item in receipt["audit"]][-1] == "submit.duplicate_prevented"


def test_known_x_branch_and_unknown_business_status_are_distinct():
    known = lab.start("happy_x")
    source = _download(known["run_id"])
    reviewed = _edit(source, resolution_code="AUTO-X", review_note="reviewed")
    lab.post("/lab/upload", {"run_id": known["run_id"], "content": reviewed})
    assert lab.receipt(known["run_id"])["state"] == "validated"

    unknown = lab.start("unknown_status")
    source = _download(unknown["run_id"])
    reviewed = _edit(source, review_note="reviewed")
    with pytest.raises(lab.LabError) as decision:
        lab.post("/lab/upload", {"run_id": unknown["run_id"], "content": reviewed})
    assert decision.value.code == "LAB_BUSINESS_DECISION_REQUIRED"
    assert decision.value.status == 409
    assert lab.receipt(unknown["run_id"])["state"] == "in_progress"


def test_office_packet_round_trips_xlsx_with_pdf_and_transient_retry():
    from openpyxl import load_workbook

    run = lab.start("office_packet")
    run_id = run["run_id"]
    headers, source = _download_bytes(run_id)
    assert headers["Content-Type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    workbook = load_workbook(io.BytesIO(source))
    assert workbook.sheetnames == ["Vendor Review", "Instructions"]
    sheet = workbook["Vendor Review"]
    sheet["C2"] = "OPS-Z"
    sheet["E2"] = "reviewed"
    reviewed = io.BytesIO()
    workbook.save(reviewed)
    payload = {
        "run_id": run_id,
        "filename": f"vendor-review-{run_id[:8]}.xlsx",
        "content_base64": base64.b64encode(reviewed.getvalue()).decode(),
    }

    pdf_status, pdf_headers, pdf = lab.get(f"/lab/instructions?run={run_id}")
    assert pdf_status == 200
    assert pdf_headers["Content-Type"] == "application/pdf"
    assert pdf.startswith(b"%PDF-1.4") and b"VENDOR REVIEW INSTRUCTIONS" in pdf

    with pytest.raises(lab.LabError) as transient:
        lab.post("/lab/upload", payload)
    assert transient.value.code == "LAB_UPLOAD_TRANSIENT"
    status, _, response = lab.post("/lab/upload", payload)
    assert status == 200 and b'"format":"xlsx"' in response
    lab.post("/lab/submit", {"run_id": run_id})
    receipt = lab.receipt(run_id)
    assert receipt["state"] == "submitted"
    assert receipt["source_format"] == "xlsx"
    assert receipt["upload_filename"].endswith(".xlsx")
    assert receipt["upload_attempts"] == 2
    assert [item["event"] for item in receipt["audit"]] == [
        "run.started",
        "document.downloaded",
        "instructions.downloaded",
        "upload.rejected",
        "upload.validated",
        "submit.committed",
    ]
