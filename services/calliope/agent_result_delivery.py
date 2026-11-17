"""Small agent receipts, with lossless retrieval from the existing durable ledgers.

UI conversation snapshots must never be used as the model's delivery envelope.
Character offsets refer to decoded text, including for single-line SQL/JSON.
"""
from __future__ import annotations
import json
from typing_extensions import TypedDict


class NamedSQLQuery(TypedDict):
    """Explicit fields survive providers that close open object schemas."""
    __pydantic_config__ = {"extra": "forbid"}
    name: str
    sql: str

DEFAULT_PAGE_CHARS = 8_000
MAX_PAGE_CHARS = 12_000


def text_page(text, offset=0, limit=DEFAULT_PAGE_CHARS):
    offset, limit = int(offset), int(limit)
    if offset < 0 or not 1 <= limit <= MAX_PAGE_CHARS:
        raise ValueError(f"offset must be nonnegative and limit between 1 and {MAX_PAGE_CHARS}")
    text = str(text or "")
    end = min(len(text), offset + limit)
    return {
        "text": text[offset:end], "offset": offset, "total_chars": len(text),
        "next_offset": end if end < len(text) else None,
    }


def specialist_receipt(value, *, response_chars=DEFAULT_PAGE_CHARS):
    # Queued/steering receipts contain delivery instructions, not UI history.
    if not isinstance(value, dict) or "response" not in value:
        return value
    page = text_page(value.get("response"), limit=response_chars)
    result = {key: value[key] for key in (
        "status", "run_id", "thread_id", "round", "round_count", "thread_status",
        "specialist", "elapsed_ms", "started_at", "completed_at", "error",
        "followup_started", "needs_result_read", "next",
    ) if key in value}
    result.update({
        "response": page["text"],
        "response_chars": page["total_chars"],
        "response_truncated": page["next_offset"] is not None,
        "stage_surface_count": len(value.get("stage_surfaces") or []),
    })
    if page["next_offset"] is not None:
        result["read_more"] = {
            "tool": "read_calliope_specialist_result",
            "arguments": {"run_id": value.get("run_id"), "offset": page["next_offset"]},
        }
        result["note"] = "Read the remaining answer using read_more; do not ask the specialist to repeat it."
    return result


def sql_statements(args):
    """Preserve exact executed statements; batch names are stable selectors."""
    args = args if isinstance(args, dict) else {}
    if "queries" in args:
        try:
            return normalize_sql_batch(args["queries"])
        except ValueError:
            return {}
    sql = args.get("sql")
    return {"sql": sql} if isinstance(sql, str) else {}


def normalize_sql_batch(queries):
    """Accept common lossless batch encodings; never silently lose a query."""
    if isinstance(queries, str):
        try:
            queries = json.loads(queries)
        except (ValueError, TypeError) as exc:
            raise ValueError('queries must be a {name: sql} object or [{name, sql}] list') from exc
    if isinstance(queries, list):
        result = {}
        for index, item in enumerate(queries):
            if not isinstance(item, dict) or set(item) != {'name', 'sql'}:
                raise ValueError(f'queries[{index}] must contain name and sql. '
                                 'Retry with [{"name":"example","sql":"SELECT 1"}]; '
                                 'empty objects are not SQL queries.')
            name = item['name']
            if not isinstance(name, str) or name in result:
                raise ValueError('Query names must be unique strings')
            result[name] = item['sql']
        queries = result
    if not isinstance(queries, dict) or not queries or len(queries) > 24:
        raise ValueError('queries must contain 1 to 24 named SQL statements')
    if any(not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
           for k, v in queries.items()):
        raise ValueError('Each query needs a nonempty name and SQL string')
    return dict(queries)
