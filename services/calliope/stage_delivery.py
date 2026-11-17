"""Shared Hermes/Calliope contract for live Stage evidence (no runtime imports).

The Docker build copies this module into Hermes too. Adding a Stage projector
must not leave the gateway silently withholding its result until turn end.
"""
import json


SQL_TOOLS = frozenset({"run_sql", "run_sql_multi", "calliope_sheet_query"})
ARTIFACT_TOOLS = frozenset({
    "publish_dashboard", "update_dashboard", "create_live_app", "update_live_app",
})
STAGE_TOOLS = frozenset({
    "prepare_calliope_email", "save_calliope_email_draft",
    *SQL_TOOLS, *ARTIFACT_TOOLS,
    "publish_answer", "publish_board", "update_board", "publish_map", "update_map",
    "publish_arrangement", "update_arrangement",
    "publish_chart", "update_chart", "find", "metric", "metric_history", "pivot",
    "cube_pivot", "describe_cube", "get_business_value_system", "calliope_decision_request",
    "request_calliope_input", "plan_calliope_action", "execute_calliope_action",
    "administer_calliope_action", "administer_local_sql",
    "run_workspace_sql", "save_workspace_job", "manage_workspace_job",
    "connect_mirror_source", "create_mirror_job", "draft_calliope_instrument",
    "draft_calliope_workflow", "draft_calliope_work_order", "draft_calliope_playbook",
    "read_calliope_playbook", "approve_calliope_playbook", "set_calliope_playbook_access",
    "archive_calliope_playbook", "create_calliope_sketch", "update_calliope_sketch",
    "capture_live_app", "render_pdf", "export_to_google_sheets",
    "run_business_logic", "execute_business_logic", "read_business_logic_receipt",
})
MEDIA_TOOLS = frozenset({
    "image_generate", "generate_image", "image_edit", "edit_image",
    "capture_image", "browser_screenshot",
})
TODO_TOOLS = frozenset({"todo", "todo_list"})
MAX_RECEIPT_BYTES = 2 * 1024 * 1024


def canonical_tool(name):
    name = str(name or "")
    for separator in ("__", ".", "/"):
        name = name.rsplit(separator, 1)[-1]
    return name


def decode_result(result):
    if isinstance(result, str) and len(result) <= MAX_RECEIPT_BYTES:
        try:
            return json.loads(result)
        except ValueError:
            pass
    return result


def failed_result(result):
    """Check protocol envelopes, never a query row or a partial SQL batch."""
    value = decode_result(result)
    if not isinstance(value, dict):
        return False
    return bool(value.get("error") or value.get("isError") or value.get("is_error")
                or value.get("success") is False)


def media_result(result):
    """Extract explicit media references from native, MCP, and multimodal envelopes."""
    refs, pending, visited = [], [(result, 0)], 0
    while pending and len(refs) < 12 and visited < 128:
        visited += 1
        value, depth = pending.pop()
        if depth > 8:
            continue
        value = decode_result(value)
        if isinstance(value, dict):
            if failed_result(value):
                continue
            for key, item in value.items():
                if key in {"image", "path", "file_path", "url", "image_url"} and isinstance(item, str):
                    if len(item) <= 4096 and item.startswith(("/", "https://", "http://")):
                        ref = {key: item}
                        if isinstance(value.get("image_id"), str) and len(value["image_id"]) == 36:
                            ref["image_id"] = value["image_id"]
                        refs.append(ref)
                elif key in {"result", "content", "text", "text_summary", "images", "files", "data", "image_url"}:
                    pending.append((item, depth + 1))
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value[:12])
    if not refs:
        return None
    return refs[0] if len(refs) == 1 else {"images": refs[:12]}


def receipt_fields(name, arguments, result):
    """Return trusted server-side projection inputs, or no live result.

    Calliope still authorizes/copies files and projects the receipt before any
    data reaches the browser. Native media never forwards base64 or prompts.
    Generic shell, file-read and source-code output stays out of this channel.
    """
    tool = canonical_tool(name)
    if failed_result(result):
        return {}
    args = arguments if isinstance(arguments, dict) else {}
    if tool in MEDIA_TOOLS:
        # Native tools return a path/URL, not a Stage surface. Preserve the
        # field names understood by the existing durable file publisher.
        media = media_result(result)
        if not media:
            return {}
        return {"arguments": {}, "result": media}
    if tool not in STAGE_TOOLS | TODO_TOOLS:
        return {}
    if tool in {"connect_mirror_source", "create_mirror_job"}:
        # Connection secrets are entered through secure UI, never replayed as
        # live tool arguments. The result is already the public action receipt.
        args = {k: v for k, v in args.items() if k in {
            "name", "title", "source_type", "connection_id", "job_id", "mode",
        }}
    fields = {"arguments": args, "result": result}
    try:
        encoded = json.dumps(fields, ensure_ascii=False)
    except (TypeError, ValueError):
        return {}
    return fields if len(encoded.encode("utf-8")) <= MAX_RECEIPT_BYTES else {}
