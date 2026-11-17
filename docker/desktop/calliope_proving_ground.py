"""Deterministic browser/computer-use proving ground for the debug desktop.

The lab is intentionally synthetic and reachable only from the desktop loopback
interface (or the authenticated control-plane connection).  It exercises the
same browser, downloads, uploads, approvals, evidence, and retry behavior as a
real vendor portal without introducing a third-party account or side effect.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import html
import io
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit


SCENARIOS = {
    "happy_x": {
        "label": "Happy path · X branch",
        "status": "X",
        "modal": False,
        "upload_failures": 0,
        "shifted": False,
        "file_format": "csv",
        "reference_pdf": False,
    },
    "happy_z": {
        "label": "Happy path · Z branch",
        "status": "Z",
        "modal": False,
        "upload_failures": 0,
        "shifted": False,
        "file_format": "csv",
        "reference_pdf": False,
    },
    "recoverable_combo": {
        "label": "Recoverable friction · modal + layout + transient upload",
        "status": "Z",
        "modal": True,
        "upload_failures": 1,
        "shifted": True,
        "file_format": "csv",
        "reference_pdf": False,
    },
    "office_packet": {
        "label": "Office packet · XLSX + PDF + transient upload",
        "status": "Z",
        "modal": False,
        "upload_failures": 1,
        "shifted": False,
        "file_format": "xlsx",
        "reference_pdf": True,
    },
    "unknown_status": {
        "label": "Material ambiguity · unknown status",
        "status": "Q-17",
        "modal": False,
        "upload_failures": 0,
        "shifted": False,
        "file_format": "csv",
        "reference_pdf": False,
    },
}


class LabError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class LabRun:
    id: str
    scenario: str
    started_at: float = field(default_factory=time.time)
    audit: list[dict[str, Any]] = field(default_factory=list)
    upload_attempts: int = 0
    upload_sha256: str | None = None
    upload_filename: str | None = None
    validation: dict[str, Any] | None = None
    submitted_at: float | None = None
    submission_id: str | None = None
    duplicate_submits: int = 0


_lock = threading.RLock()
_run: LabRun | None = None


def _scenario(name: Any) -> tuple[str, dict[str, Any]]:
    key = str(name or "").strip()
    if key not in SCENARIOS:
        raise LabError("LAB_SCENARIO_INVALID", "Choose a supported proving-ground scenario.")
    return key, SCENARIOS[key]


def _run_id(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError):
        raise LabError("LAB_RUN_INVALID", "The proving-ground run is no longer available.", 404) from None


def _event(run: LabRun, kind: str, detail: dict[str, Any] | None = None) -> None:
    if len(run.audit) >= 500:
        return
    run.audit.append(
        {
            "sequence": len(run.audit) + 1,
            "event": kind,
            "elapsed_ms": round((time.time() - run.started_at) * 1000),
            "detail": detail or {},
        }
    )


def reset() -> None:
    global _run
    with _lock:
        _run = None


def start(scenario: Any, run_id: Any = None) -> dict[str, Any]:
    global _run
    key, config = _scenario(scenario)
    rid = _run_id(run_id) if run_id else str(uuid.uuid4())
    with _lock:
        _run = LabRun(id=rid, scenario=key)
        _event(_run, "run.started", {"scenario": key, "status": config["status"]})
    return {
        "run_id": rid,
        "scenario": key,
        "label": config["label"],
        "url": f"http://127.0.0.1:9000/lab?{urlencode({'run': rid})}",
    }


def _current(run_id: Any) -> tuple[LabRun, dict[str, Any]]:
    rid = _run_id(run_id)
    with _lock:
        if not _run or _run.id != rid:
            raise LabError("LAB_RUN_NOT_FOUND", "This proving-ground run has expired.", 404)
        return _run, SCENARIOS[_run.scenario]


def _csv_bytes(run: LabRun, config: dict[str, Any]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=["record_id", "status", "owner_code", "resolution_code", "review_note"],
    )
    writer.writeheader()
    writer.writerow(
        {
            "record_id": f"VR-{run.id[:8].upper()}",
            "status": config["status"],
            "owner_code": "UNASSIGNED",
            "resolution_code": "",
            "review_note": "",
        }
    )
    return output.getvalue().encode("utf-8")


def _xlsx_bytes(run: LabRun, config: dict[str, Any]) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
    except ImportError as exc:
        raise LabError("LAB_XLSX_UNAVAILABLE", "XLSX support is not installed in this desktop.", 503) from exc
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Vendor Review"
    headers = ["record_id", "status", "owner_code", "resolution_code", "review_note"]
    sheet.append(headers)
    sheet.append([f"VR-{run.id[:8].upper()}", config["status"], "UNASSIGNED", "", ""])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F725B")
        cell.alignment = Alignment(horizontal="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = "A1:E2"
    for key, width in {"A": 22, "B": 14, "C": 18, "D": 22, "E": 24}.items():
        sheet.column_dimensions[key].width = width
    instructions = workbook.create_sheet("Instructions")
    instructions.append(["Vendor review operating rule"])
    instructions.append(["For X, set resolution_code to AUTO-X."])
    instructions.append(["For Z, set owner_code to OPS-Z."])
    instructions.append(["For either known status, set review_note to reviewed."])
    instructions.append(["Never change record_id or status. Uncovered statuses require a human decision."])
    instructions.column_dimensions["A"].width = 92
    instructions["A1"].font = Font(bold=True, size=14, color="1F725B")
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _source_bytes(run: LabRun, config: dict[str, Any]) -> bytes:
    return _xlsx_bytes(run, config) if config["file_format"] == "xlsx" else _csv_bytes(run, config)


def _pdf_bytes(config: dict[str, Any]) -> bytes:
    lines = [
        "VENDOR REVIEW INSTRUCTIONS",
        f"Queue status for this packet: {config['status']}",
        "For X: set resolution_code to AUTO-X.",
        "For Z: set owner_code to OPS-Z.",
        "For either known status: set review_note to reviewed.",
        "Never change record_id or status.",
        "If the status is not covered, park the case for a human decision.",
        "Upload validation is reversible. Submit is consequential and exactly-once.",
    ]
    escaped = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
    stream = ["BT", "/F1 12 Tf", "54 738 Td", "16 TL"]
    for index, line in enumerate(escaped):
        if index:
            stream.append("T*")
        stream.append(f"({line}) Tj")
    stream.append("ET")
    content = "\n".join(stream).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, item in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode())
        output.extend(item)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def _validate_csv(content: str, run: LabRun, config: dict[str, Any]) -> dict[str, Any]:
    try:
        rows = list(csv.DictReader(io.StringIO(content)))
    except csv.Error as exc:
        raise LabError("LAB_CSV_INVALID", f"CSV could not be read: {exc}") from None
    if len(rows) != 1:
        raise LabError("LAB_CSV_INVALID", "The upload must contain exactly one data row.")
    row = {str(key or ""): str(value or "").strip() for key, value in rows[0].items()}
    expected_id = f"VR-{run.id[:8].upper()}"
    if row.get("record_id") != expected_id or row.get("status") != config["status"]:
        raise LabError("LAB_CSV_IDENTITY_CHANGED", "Record identity and source status must remain unchanged.")
    status = config["status"]
    errors: list[str] = []
    if status == "X":
        if row.get("resolution_code") != "AUTO-X":
            errors.append("Status X requires resolution_code AUTO-X.")
    elif status == "Z":
        if row.get("owner_code") != "OPS-Z":
            errors.append("Status Z requires owner_code OPS-Z.")
    else:
        raise LabError(
            "LAB_BUSINESS_DECISION_REQUIRED",
            f"Status {status} is not covered by the operating procedure. Park it for a human decision.",
            409,
        )
    if row.get("review_note").lower() != "reviewed":
        errors.append("review_note must be reviewed.")
    if errors:
        raise LabError("LAB_CSV_VALIDATION_FAILED", " ".join(errors), 422)
    return {"record_id": expected_id, "status": status, "row": row}


def _validate_xlsx(content: bytes, run: LabRun, config: dict[str, Any]) -> dict[str, Any]:
    try:
        from openpyxl import load_workbook

        workbook = load_workbook(io.BytesIO(content), data_only=False, read_only=True)
    except Exception as exc:
        raise LabError("LAB_XLSX_INVALID", f"Workbook could not be read: {exc}") from None
    if "Vendor Review" not in workbook.sheetnames:
        raise LabError("LAB_XLSX_SHEET_MISSING", "The workbook must retain the Vendor Review sheet.")
    sheet = workbook["Vendor Review"]
    values = list(sheet.iter_rows(values_only=True))
    if len(values) != 2:
        raise LabError("LAB_XLSX_INVALID", "The Vendor Review sheet must contain one header and exactly one data row.")
    headers = [str(value or "").strip() for value in values[0]]
    expected = ["record_id", "status", "owner_code", "resolution_code", "review_note"]
    if headers != expected:
        raise LabError("LAB_XLSX_SCHEMA_CHANGED", "The Vendor Review columns and their order must remain unchanged.")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=expected)
    writer.writeheader()
    writer.writerow(dict(zip(expected, values[1], strict=True)))
    return _validate_csv(output.getvalue(), run, config)


def _uploaded_bytes(body: dict[str, Any]) -> tuple[str, bytes]:
    filename = str(body.get("filename") or "reviewed.csv").strip()
    encoded = body.get("content_base64")
    if encoded not in (None, ""):
        try:
            content = base64.b64decode(str(encoded), validate=True)
        except (ValueError, TypeError):
            raise LabError("LAB_UPLOAD_INVALID", "The uploaded file payload is not valid base64.") from None
    else:
        content = str(body.get("content") or "").encode("utf-8")
    return filename, content


def _validate_document(content: bytes, filename: str, run: LabRun, config: dict[str, Any]) -> dict[str, Any]:
    expected_format = str(config["file_format"])
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix != expected_format:
        raise LabError("LAB_UPLOAD_FORMAT_CHANGED", f"Upload the reviewed .{expected_format} file without converting its format.")
    if expected_format == "xlsx":
        value = _validate_xlsx(content, run, config)
    else:
        try:
            value = _validate_csv(content.decode("utf-8-sig"), run, config)
        except UnicodeDecodeError:
            raise LabError("LAB_CSV_INVALID", "CSV must be UTF-8 encoded.") from None
    value["format"] = expected_format
    value["filename"] = filename
    return value


def _receipt(run: LabRun, config: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "scenario": run.scenario,
        "scenario_label": config["label"],
        "source_status": config["status"],
        "source_format": config["file_format"],
        "state": "submitted" if run.submitted_at else "validated" if run.validation else "in_progress",
        "upload_attempts": run.upload_attempts,
        "upload_sha256": run.upload_sha256,
        "upload_filename": run.upload_filename,
        "validation": run.validation,
        "submission_id": run.submission_id,
        "submitted_at_epoch": run.submitted_at,
        "duplicate_submits": run.duplicate_submits,
        "audit": list(run.audit),
    }


def receipt(run_id: Any) -> dict[str, Any]:
    with _lock:
        run, config = _current(run_id)
        return _receipt(run, config)


def _json_response(value: dict[str, Any], status: int = 200) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(value, separators=(",", ":"), default=str).encode()
    return status, {"Content-Type": "application/json", "Cache-Control": "no-store"}, body


def get(path: str) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(path)
    query = parse_qs(parts.query)
    run_id = (query.get("run") or [""])[0]
    with _lock:
        run, config = _current(run_id)
        if parts.path == "/lab":
            _event(run, "page.viewed", {"scenario": run.scenario})
            body = _page(run, config).encode("utf-8")
            return 200, {
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'",
            }, body
        if parts.path == "/lab/download":
            content = _source_bytes(run, config)
            file_format = str(config["file_format"])
            _event(run, "document.downloaded", {"format": file_format, "sha256": hashlib.sha256(content).hexdigest()})
            return 200, {
                "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if file_format == "xlsx" else "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="vendor-review-{run.id[:8]}.{file_format}"',
                "Content-Length": str(len(content)),
                "Cache-Control": "no-store",
            }, content
        if parts.path == "/lab/instructions" and config.get("reference_pdf"):
            content = _pdf_bytes(config)
            _event(run, "instructions.downloaded", {"format": "pdf", "sha256": hashlib.sha256(content).hexdigest()})
            return 200, {
                "Content-Type": "application/pdf",
                "Content-Disposition": f'attachment; filename="vendor-review-instructions-{run.id[:8]}.pdf"',
                "Content-Length": str(len(content)),
                "Cache-Control": "no-store",
            }, content
        if parts.path == "/lab/receipt":
            return _json_response(_receipt(run, config))
    raise LabError("NOT_FOUND", "Endpoint not found.", 404)


def post(path: str, body: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    parts = urlsplit(path)
    run_id = body.get("run_id")
    with _lock:
        run, config = _current(run_id)
        if parts.path == "/lab/acknowledge":
            _event(run, "notice.acknowledged", {"notice": "synthetic-policy-update"})
            return _json_response({"ok": True})
        if parts.path == "/lab/upload":
            run.upload_attempts += 1
            filename, content = _uploaded_bytes(body)
            if len(content) > 256_000:
                raise LabError("LAB_UPLOAD_TOO_LARGE", "The synthetic upload is limited to 256 KB.", 413)
            if run.upload_attempts <= int(config["upload_failures"]):
                _event(run, "upload.rejected", {"attempt": run.upload_attempts, "reason": "transient_gateway"})
                raise LabError(
                    "LAB_UPLOAD_TRANSIENT",
                    "The vendor upload gateway timed out. The file was not accepted; retry is safe.",
                    503,
                )
            validation = _validate_document(content, filename, run, config)
            digest = hashlib.sha256(content).hexdigest()
            run.upload_sha256 = digest
            run.upload_filename = filename
            run.validation = validation
            _event(run, "upload.validated", {"attempt": run.upload_attempts, "sha256": digest})
            return _json_response({"ok": True, "validation": validation, "sha256": digest})
        if parts.path == "/lab/submit":
            if not run.validation:
                raise LabError("LAB_SUBMIT_NOT_READY", "Upload and validate the reviewed CSV first.", 409)
            if run.submitted_at:
                run.duplicate_submits += 1
                _event(run, "submit.duplicate_prevented", {"submission_id": run.submission_id})
                return _json_response({"ok": True, "duplicate": True, "receipt": _receipt(run, config)})
            run.submitted_at = time.time()
            run.submission_id = f"SUB-{run.id[:12].upper()}"
            _event(run, "submit.committed", {"submission_id": run.submission_id})
            return _json_response({"ok": True, "duplicate": False, "receipt": _receipt(run, config)})
    raise LabError("NOT_FOUND", "Endpoint not found.", 404)


def _page(run: LabRun, config: dict[str, Any]) -> str:
    record_id = f"VR-{run.id[:8].upper()}"
    status = html.escape(str(config["status"]))
    modal = "" if config["modal"] else "hidden"
    shifted = " shifted" if config["shifted"] else ""
    file_format = str(config["file_format"])
    format_label = "Excel workbook" if file_format == "xlsx" else "CSV"
    reference = f'<a href="/lab/instructions?run={run.id}"><button type="button">Download instructions PDF</button></a>' if config.get("reference_pdf") else ""
    rule = (
        "For X, set resolution_code to AUTO-X. For Z, set owner_code to OPS-Z. "
        "For either known status, set review_note to reviewed. Never change record_id or status. "
        "If the status is not covered, do not guess: park the case for a human decision."
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Vendor Operations Sandbox</title><style>
:root{{--ink:#17201d;--muted:#66736d;--line:#d9e0dc;--paper:#fbfcfa;--jade:#1f725b;--pale:#e8f2ed;--amber:#9b650f;--red:#a73f35}}
*{{box-sizing:border-box}}body{{margin:0;background:#eef1ee;color:var(--ink);font:15px/1.45 ui-sans-serif,system-ui,sans-serif}}
header{{height:64px;display:flex;align-items:center;gap:13px;padding:0 32px;background:#14231e;color:white}}
.mark{{display:grid;place-items:center;width:34px;height:34px;border:1px solid #65917f;color:#9ed2bd;font-weight:800}}
header strong{{font-size:15px}}header small{{display:block;color:#9fbaaf;font-size:11px;letter-spacing:.08em;text-transform:uppercase}}
main{{max-width:1030px;margin:28px auto;padding:0 24px}}.eyebrow{{color:var(--jade);font-size:11px;font-weight:800;letter-spacing:.1em;text-transform:uppercase}}
h1{{margin:5px 0 3px;font:700 30px/1.15 Georgia,serif}}.sub{{margin:0 0 22px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:1.45fr .85fr;gap:18px}}.card{{border:1px solid var(--line);background:var(--paper);padding:22px;box-shadow:0 8px 24px #1b302315}}
.record{{display:grid;grid-template-columns:1fr 1fr;gap:1px;margin:18px 0;background:var(--line);border:1px solid var(--line)}}
.fact{{background:white;padding:14px}}.fact small{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.08em}}.fact b{{display:block;margin-top:4px;font:700 18px ui-monospace,monospace}}
.status{{color:var(--amber)}}.rule{{border-left:3px solid var(--jade);background:var(--pale);padding:13px 15px;font-size:13px}}
.steps{{display:grid;gap:11px;margin-top:18px}}.step{{display:grid;grid-template-columns:27px 1fr;gap:10px;align-items:start}}.step i{{display:grid;place-items:center;width:25px;height:25px;border:1px solid var(--line);border-radius:50%;font-style:normal;font-size:11px}}.step b{{display:block;font-size:13px}}.step span{{color:var(--muted);font-size:12px}}
button,.upload{{border:1px solid #b9c7c0;background:white;color:var(--ink);padding:10px 13px;font-weight:700;cursor:pointer}}button.primary{{background:var(--jade);border-color:var(--jade);color:white}}button:disabled{{opacity:.45;cursor:not-allowed}}
.controls{{display:flex;gap:9px;align-items:center;margin-top:18px;flex-wrap:wrap}}.controls.shifted{{justify-content:flex-end;flex-direction:row-reverse}}input[type=file]{{max-width:240px;font-size:12px}}#message{{min-height:38px;margin-top:13px;padding:9px 11px;border:1px solid transparent;font-size:12px}}#message.ok{{border-color:#90bba9;background:#edf7f2;color:#185d49}}#message.error{{border-color:#d7a59f;background:#fff1ef;color:var(--red)}}
.audit{{margin:0;padding:0;list-style:none}}.audit li{{padding:10px 0;border-bottom:1px solid var(--line);font:12px ui-monospace,monospace;color:var(--muted)}}.audit b{{color:var(--ink)}}
.receipt{{margin-top:17px;border:1px dashed #aebbb5;padding:13px;background:white;font:12px ui-monospace,monospace;word-break:break-all}}
.modal{{position:fixed;inset:0;z-index:5;display:grid;place-items:center;background:#10201bb8}}.modal[hidden]{{display:none}}.dialog{{width:min(480px,90vw);border:1px solid #8aa195;background:white;padding:25px;box-shadow:0 24px 90px #0005}}.dialog h2{{margin:0 0 8px;font:700 23px Georgia,serif}}.dialog p{{color:var(--muted)}}
@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body>
<header><div class="mark">VO</div><div><small>Synthetic proving ground</small><strong>Vendor Operations Sandbox</strong></div></header>
<main><div class="eyebrow">Case reconciliation · {html.escape(config['label'])}</div><h1>Review one vendor case</h1><p class="sub">Download the source {format_label}, apply the operating rule, upload the reviewed file, then submit only after preview validation succeeds.</p>
<div class="grid"><section class="card"><div class="record"><div class="fact"><small>Record ID</small><b>{record_id}</b></div><div class="fact"><small>Queue status</small><b class="status">{status}</b></div></div>
<div class="rule"><b>Operating rule</b><br>{html.escape(rule)}</div>
<div class="controls{shifted}"><a href="/lab/download?run={run.id}"><button type="button">Download source {html.escape(format_label)}</button></a>{reference}<input id="file" type="file" accept=".{file_format}"><button id="upload" type="button">Upload &amp; preview</button><button id="submit" type="button" class="primary" disabled>Submit reviewed case</button></div><div id="message"></div><div id="receipt" class="receipt" hidden></div>
</section><aside class="card"><div class="eyebrow">Run ledger</div><div class="steps"><div class="step"><i>1</i><div><b>Inspect</b><span>Read status and rule.</span></div></div><div class="step"><i>2</i><div><b>Prepare</b><span>Edit the downloaded CSV.</span></div></div><div class="step"><i>3</i><div><b>Validate</b><span>Upload; a rejected upload is not a commit.</span></div></div><div class="step"><i>4</i><div><b>Commit</b><span>Submission is consequential and exactly-once.</span></div></div></div><ul class="audit" id="audit"></ul></aside></div></main>
<div class="modal" id="modal" {modal}><div class="dialog"><div class="eyebrow">Routine notice</div><h2>Portal layout updated</h2><p>The upload controls moved, but the operating rule and case data are unchanged. Acknowledge this notice to continue.</p><button id="ack" class="primary" type="button">Acknowledge and continue</button></div></div>
<script>
const runId={json.dumps(run.id)};const message=document.querySelector('#message');const submit=document.querySelector('#submit');
function show(text,kind=''){{message.textContent=text;message.className=kind}}
async function call(path,body={{}}){{const response=await fetch(path,{{method:'POST',headers:{{'content-type':'application/json'}},body:JSON.stringify({{run_id:runId,...body}})}});const value=await response.json();if(!response.ok)throw new Error(value.error?.message||'Request failed');return value}}
document.querySelector('#ack').addEventListener('click',async()=>{{await call('/lab/acknowledge');document.querySelector('#modal').hidden=true;show('Notice acknowledged. The case is unchanged.','ok')}});
function fileBase64(file){{return new Promise((resolve,reject)=>{{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',',2)[1]||'');reader.onerror=()=>reject(reader.error);reader.readAsDataURL(file)}})}}
document.querySelector('#upload').addEventListener('click',async()=>{{const file=document.querySelector('#file').files[0];if(!file)return show('Choose the reviewed file first.','error');show('Checking upload…');try{{const value=await call('/lab/upload',{{filename:file.name,content_base64:await fileBase64(file)}});submit.disabled=false;show(`Preview valid · ${{value.validation.record_id}} · ${{value.validation.format.toUpperCase()}} · SHA ${{value.sha256.slice(0,12)}}`,'ok')}}catch(error){{submit.disabled=true;show(error.message,'error')}}}});
submit.addEventListener('click',async()=>{{submit.disabled=true;try{{const value=await call('/lab/submit');const receipt=value.receipt;show(value.duplicate?'Duplicate submit prevented; original receipt retained.':'Reviewed case submitted.','ok');const box=document.querySelector('#receipt');box.hidden=false;box.textContent=`${{receipt.submission_id}} · ${{receipt.upload_sha256}}`}}catch(error){{submit.disabled=false;show(error.message,'error')}}}});
</script></body></html>"""
