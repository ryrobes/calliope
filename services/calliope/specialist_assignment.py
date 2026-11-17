"""Bounded handoffs and small progress receipts, independent of model prose."""
import json
import shlex
import tool_arguments


SECTIONS = ("settled_decisions", "supplied_evidence", "implementation_questions", "deliverables")
ASSIGNMENT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": list(SECTIONS),
    "properties": {key: {"type": "array", "items": {"type": "string", "minLength": 1}} for key in SECTIONS},
}
CONTEXT_SCHEMA = {"properties": {"assignment": ASSIGNMENT_SCHEMA}}


class AssignmentValidationError(ValueError):
    """A caller can repair this input; it is not a server outage."""

ROUND_INSTRUCTION = """ASSIGNMENT BOUNDARY — APPLIES EVERY ROUND
Calliope owns the overall task, story, coordination, and Playbook. You own only
this assignment. Supplied decisions, SQL, findings, design references, and prior
reviews are inputs to use, not an invitation to independently redo that work.
If an assignment object is present, its settled_decisions and supplied_evidence
are established inputs; implementation_questions are the remaining open work;
deliverables define completion. Empty sections do not erase the task text or
the pinned current-turn context. The latest explicit user/Calliope correction
takes precedence over an earlier decision.
Do only the discovery needed to answer a specific missing implementation fact.
Integration checks are allowed; a preference for another story or architecture
is not a contradiction. If evidence contradicts a settled decision, identify
that exact decision and concrete contradicting evidence via ask_calliope
(decision_to_reopen and contradicting_evidence). Do not silently replace the
decision or take over the whole task. In a synchronous round, include those
same facts in your final reply. End a blocked round with the precise question.
For a build with adequate inputs, make the smallest useful working artifact
first, then validate and refine it. Keep native TODO milestones about the
assigned deliverables. Report what was written, validated, or published and
the specific blocker when there is none. Never claim success from tool use alone.
END ASSIGNMENT BOUNDARY"""


def assignment_from_context(context, previous=None):
    """An omitted contract inherits; an explicit one replaces it, without guessing."""
    raw = context.get("assignment") if isinstance(context, dict) else None
    if isinstance(context, dict) and "assignment" in context:
        raw = tool_arguments.normalize(raw, ASSIGNMENT_SCHEMA)
        if not isinstance(raw, dict) or set(raw) != set(SECTIONS):
            raise AssignmentValidationError("context.assignment requires exactly: " + ", ".join(SECTIONS))
        for key in SECTIONS:
            values = raw[key]
            if not isinstance(values, list):
                raise AssignmentValidationError(
                    f'context.assignment.{key} must be an array of nonempty strings, '
                    f'for example ["Use the supplied query"], not {type(values).__name__}; '
                    'use [] when the section is empty'
                )
            for index, value in enumerate(values):
                if not isinstance(value, str) or not value.strip():
                    raise AssignmentValidationError(
                        f'context.assignment.{key}[{index}] must be a nonempty string; '
                        'put each decision or evidence item directly in the array'
                    )
        if len(json.dumps(raw, ensure_ascii=False)) > 24000:
            raise AssignmentValidationError("context.assignment exceeds 24000 characters; use evidence references")
        return {key: list(raw[key]) for key in SECTIONS}
    return assignment_from_context({"assignment": previous}) if previous is not None else None


def scope_deviation(decision, evidence):
    decision, evidence = str(decision or "").strip(), str(evidence or "").strip()
    if bool(decision) != bool(evidence):
        raise ValueError("Reopening a decision requires both decision_to_reopen and contradicting_evidence")
    if max(len(decision), len(evidence)) > 6000:
        raise ValueError("Decision and evidence must each be at most 6000 characters")
    return {"decision": decision, "evidence": evidence} if decision else None


def result_object(value):
    """Unwrap native JSON and MCP envelopes; errors are never success receipts."""
    for _ in range(6):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (ValueError, TypeError):
                return {}
        if not isinstance(value, dict):
            return {}
        if value.get("error") or value.get("isError") or value.get("success") is False or value.get("ok") is False:
            return {}
        if set(value) == {"result"}:
            value = value["result"]
        elif isinstance(value.get("structuredContent"), dict):
            value = value["structuredContent"]
        elif isinstance(value.get("content"), list):
            texts = [part.get("text") for part in value["content"] if isinstance(part, dict) and part.get("type") == "text"]
            if len(texts) != 1:
                return {}
            value = texts[0]
        else:
            return value
    return {}


def tool_receipt(name, event, result, arguments):
    """Recognize explicit receipts only. Shell commands are not proof of writes."""
    if event != "tool.completed":
        return None
    name = str(name).rsplit("__", 1)[-1]
    data = result_object(result)
    args = result_object(arguments)
    if name == "tool_call" and args.get("name"):
        return tool_receipt(args["name"], event, result, args.get("arguments") or {})
    if not data:
        return None
    if name in {"write_file", "patch"} and data.get("files_modified"):
        return {"milestone": "file_written", "label": "File write confirmed"}
    if name == "terminal":
        try:
            command = shlex.split(str(args.get("command") or ""))
        except ValueError:
            return None
        # A pipeline / compound command can hide the check's exit status.
        if len(command) > 1 and command[0].rsplit("/", 1)[-1] == "calliope-web-check" and all(
            all(c not in arg for c in ";|&<>\n") for arg in command[1:]
        ) and isinstance(data.get("exit_code"), int):
            passed = data["exit_code"] == 0
            return {"milestone": "validation_passed" if passed else "validation_failed",
                    "label": "Web check passed" if passed else "Web check failed"}
    if name == "upload_artifact" and data.get("artifact_id"):
        return {"milestone": "uploaded", "label": "Source uploaded"}
    if name in {"publish_dashboard", "update_dashboard", "create_live_app", "update_live_app"} and data.get("slug") and data.get("version"):
        return {"milestone": "published", "label": "App version published"}
    return None


def progress_summary(rows):
    """First/latest milestone and latest reported deviation, without raw payloads."""
    milestones, deviation = {}, None
    for row in rows:
        payload = row.get("payload") or {}
        receipt = payload.get("work_receipt")
        when = row.get("created_at")
        when = when.isoformat() if hasattr(when, "isoformat") else str(when or "")
        if isinstance(receipt, dict) and receipt.get("milestone"):
            key = receipt["milestone"]
            first = milestones.get(key, {}).get("first_at", when)
            milestones[key] = {**receipt, "first_at": first, "last_at": when}
        if isinstance(payload.get("scope_deviation"), dict):
            deviation = {**payload["scope_deviation"], "created_at": when}
    return {"milestones": list(milestones.values()), "scope_deviation": deviation}
