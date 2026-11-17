"""Routes: how a question got answered, mined from Callie's own work.

A route is a solved question (or a knowingly unsolvable one): the tables, the
query that worked, the gotchas and dead ends, and how the person reacted. It
is distilled from a tool-heavy turn so the next person who asks something
similar skips the search.

The miner finds candidate turns in the Hermes session mirror, compacts each
into a trace and asks one LLM call (strict JSON, validated, one retry) for the
route. A kept route is checked by running its read-only probe, stored in
rvbbit.calliope_routes, and written verbatim into its own Hindsight bank.
When a later turn resembles it, Calliope recalls the route from that bank and
puts it in front of Callie, but only routes still checked good and, in
pg-auth installs, only those whose tables the speaker can read.

    python memory_routes.py --session calliope_1792481198_2e8ba40a11 [--session ...]
    python memory_routes.py --since 2026-10-20 --limit 10
    python memory_routes.py --mine            # store, check and write back new routes
    python memory_routes.py --recheck         # re-run probes that are due
    python memory_routes.py --recall "orders by region last month"

CALLIOPE_ROUTES=off turns mining and recall off.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import threading
import time
from typing import Any, Callable, Optional
from urllib.parse import quote

MIN_TOOL_RESULTS = 6
MODEL = os.environ.get("CALLIOPE_MEMORY_MODEL", "calliope-luna")
ROUTES_BANK = os.environ.get("CALLIOPE_ROUTES_BANK", "calliope-routes")
SETTLE_SECONDS = 600  # the person gets time to react before a turn is mined
TICK_SECONDS = 600
MAX_PER_TICK = 6
LOOKBACK = dt.timedelta(days=3)
RECALL_LIMIT = 2
# Looking up a tool's schema is not data discovery.
NOISE_TOOLS = {"tool_describe", "tool_search", "todo", "todo_list"}
OUTCOMES = ("answered", "dead_end", "corrected_then_answered", "abandoned")
REACTIONS = ("accepted", "corrected", "none")
_WRAPPER = re.compile(r"^\s*<untrusted_tool_result[^>]*>\s*(?:The following content[^\n]*\n)?|</untrusted_tool_result>\s*$")
_READ_ONLY = re.compile(r"^\s*(with|select|values|table|show|explain)\b", re.I)

ROUTE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["worth_keeping", "reason", "question", "intent", "outcome", "tables", "sql",
                 "result_summary", "gotchas", "dead_ends", "probe", "next_time"],
    "properties": {
        "worth_keeping": {"type": "boolean"},
        "reason": {"type": "string"},
        "question": {"type": "string"},
        "intent": {"type": "string"},
        "outcome": {"type": "string", "enum": list(OUTCOMES)},
        "tables": {"type": "array", "items": {"type": "string"}},
        "sql": {"type": ["string", "null"]},
        "result_summary": {"type": ["string", "null"]},
        "gotchas": {"type": "array", "items": {"type": "string"}},
        "dead_ends": {"type": "array", "items": {"type": "string"}},
        "probe": {"type": ["object", "null"], "additionalProperties": False,
                  "required": ["sql", "expect"],
                  "properties": {"sql": {"type": "string"}, "expect": {"type": "string"}}},
        "next_time": {"type": "string"},
    },
}

PROMPT = """You distill how an AI data analyst (Callie) answered one question, so that the
next time anyone asks something similar she can skip the search.

You get the question, every tool call she made with a short excerpt of each result,
any queries saved from the turn, her final reply, and the person's next message.

Return the route as JSON:
- worth_keeping: false when nothing here would save the next person time (small talk,
  a trivial lookup, work that depends entirely on this one conversation).
- question: the question in the asker's own words, short. intent: a neutral restatement
  someone else might phrase differently.
- outcome: answered | dead_end (the data is not there) | corrected_then_answered | abandoned.
- tables: only tables that appear in the trace and mattered to the outcome.
- sql: the final query that produced the accepted answer, copied exactly from the trace,
  or null.
- result_summary: what that query returned, briefly, or null.
- gotchas: facts about the data she had to discover ("orders_v2 is empty").
- dead_ends: searches that found nothing, so they need not be repeated.
- probe: a read-only query that re-checks this route later. For an answer, usually the
  final sql; for a dead end, a query showing the data is still missing (for example
  against information_schema), with what it should return. Null if none fits.
- next_time: one or two sentences telling Callie what to do next time.
Use only names, tables and SQL that appear in the trace. Do not invent anything."""


# -- reading the mirror -------------------------------------------------------

def _rows(conn: Any, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _session_messages(conn: Any, hermes_session: str) -> list[dict]:
    return _rows(conn, "SELECT id,role,content,tool_name,tool_calls,ts FROM rvbbit.hermes_messages "
                       "WHERE session_id=%s ORDER BY ts,id", (hermes_session,))


def _segments(messages: list[dict]) -> list[tuple[int, int]]:
    """(start, end) message ranges, one per user message."""
    starts = [i for i, m in enumerate(messages) if m["role"] == "user"]
    return [(start, (starts[k + 1] if k + 1 < len(starts) else len(messages))) for k, start in enumerate(starts)]


def load_windows(conn: Any, *, sessions: Optional[list[str]] = None, since: Optional[str] = None,
                 min_tool_results: int = MIN_TOOL_RESULTS, limit: int = 40) -> list[dict]:
    """Tool-heavy turn windows with the Calliope context each needs."""
    if sessions:
        hermes = sessions
    else:
        # Scheduled duty cycles (cron_*) answer nobody's question.
        hermes = [r["session_id"] for r in _rows(conn, """
            SELECT session_id FROM rvbbit.hermes_messages
             WHERE (%s::timestamptz IS NULL OR ts >= %s::timestamptz) AND session_id NOT LIKE 'cron\\_%%'
             GROUP BY session_id HAVING count(*) FILTER (WHERE role='tool') >= %s
             ORDER BY max(ts) DESC LIMIT %s""", (since, since, min_tool_results, limit))]
    windows = []
    for hermes_session in hermes:
        messages = _session_messages(conn, hermes_session)
        profile = (_rows(conn, "SELECT profile_name FROM rvbbit.hermes_sessions WHERE id=%s",
                         (hermes_session,)) or [{}])[0].get("profile_name") or "default"
        turns = _rows(conn, """
            SELECT t.id,t.ordinal,t.turn_kind,t.user_message,t.assistant_message,t.author_display_name,
                   t.hermes_message_offset,t.created_at,t.response_receipt->'citations' AS citations,
                   s.id AS calliope_session
              FROM rvbbit.calliope_turns t JOIN rvbbit.calliope_sessions s ON s.id=t.session_id
             WHERE s.hermes_session_id=%s ORDER BY t.ordinal""", (hermes_session,))
        by_offset = {t["hermes_message_offset"]: t for t in turns if t["hermes_message_offset"] is not None}
        for start, end in _segments(messages):
            window = messages[start:end]
            tool_results = sum(1 for m in window if m["role"] == "tool" and m["tool_name"] not in NOISE_TOOLS)
            if tool_results < min_tool_results:
                continue
            turn = by_offset.get(start)
            windows.append({
                "hermes_session": hermes_session, "profile": profile, "messages": window,
                "tool_results": tool_results, "started_at": window[0]["ts"], "ended_at": window[-1]["ts"],
                "turn": turn, "surfaces": _surfaces(conn, turn), "reaction": _reaction(turn, turns),
            })
    return windows


def _surfaces(conn: Any, turn: Optional[dict]) -> list[dict]:
    if not turn:
        return []
    return _rows(conn, """
        SELECT title, source->>'sql' AS sql, payload->'row_count' AS row_count,
               payload->'lineage'->'warehouse_objects' AS tables, payload->'metadata_query' AS metadata_query
          FROM rvbbit.calliope_surfaces WHERE turn_id=%s AND kind='query' ORDER BY ordinal""", (turn["id"],))


def _reaction(turn: Optional[dict], turns: list[dict]) -> Optional[dict]:
    """The next thing a person said after this turn, if anything."""
    if not turn:
        return None
    later = [t for t in turns if t["ordinal"] > turn["ordinal"] and t["turn_kind"] in ("chat", "note")]
    if not later:
        return None
    return {"by": later[0]["author_display_name"], "text": (later[0]["user_message"] or "")[:400]}


# -- compacting a turn into a trace -------------------------------------------

def _unwrap(value: Any) -> Any:
    """Tool results arrive wrapped and often as JSON inside a JSON string."""
    text = _WRAPPER.sub("", value or "").strip() if isinstance(value, str) else value
    for _ in range(2):
        if not isinstance(text, str):
            break
        try:
            parsed = json.loads(text)
        except ValueError:
            break
        text = parsed.get("result", parsed) if isinstance(parsed, dict) and set(parsed) == {"result"} else parsed
    return text


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"), default=str)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _calls(message: dict) -> list[tuple[str, Any]]:
    raw = message.get("tool_calls")
    calls = raw if isinstance(raw, list) else json.loads(raw) if isinstance(raw, str) and raw else []
    out = []
    for call in calls:
        function = (call or {}).get("function") or {}
        name, args = function.get("name") or "?", _unwrap(function.get("arguments"))
        if name == "tool_call" and isinstance(args, dict):  # the bridge wraps the real calls
            out.extend((inner.get("name") or "?", inner.get("arguments")) for inner in args.get("calls") or [])
        else:
            out.append((name, args))
    return out


def compact_trace(window: dict, *, result_chars: int = 500, arg_chars: int = 400) -> dict:
    """What the distiller sees, plus every identifier it may legitimately name."""
    steps, final = [], ""
    for message in window["messages"][1:]:
        if message["role"] == "assistant":
            calls = [c for c in _calls(message) if c[0] not in NOISE_TOOLS]
            steps.extend(f"CALL {name} {_short(args, arg_chars)}" for name, args in calls)
            if not message.get("tool_calls") and (message.get("content") or "").strip():
                final = message["content"]
        elif message["role"] == "tool" and message.get("tool_name") not in NOISE_TOOLS:
            steps.append(f"RESULT {message['tool_name']} {_short(_unwrap(message.get('content')), result_chars)}")
    turn = window.get("turn") or {}
    question = (turn.get("user_message") or _unwrap(window["messages"][0].get("content")) or "")[:1500]
    asker = turn.get("author_display_name") or (f"Callie, delegating to {window['profile']}"
                                                if window["profile"] != "default" else "someone")
    queries = [{"title": s["title"], "sql": s["sql"], "rows": s["row_count"], "tables": s["tables"],
                "exploration": s["metadata_query"]} for s in window.get("surfaces") or [] if s.get("sql")]
    trace = {
        "asked_by": asker, "question": question, "steps": steps, "saved_queries": queries,
        "final_reply": (final or turn.get("assistant_message") or "")[:1500],
        "next_message": window.get("reaction"),
    }
    return {"trace": trace, "text": json.dumps(trace, default=str)}


# -- distilling ----------------------------------------------------------------

def validate_route(route: Any, trace_text: str) -> list[str]:
    """Problems with a route; empty means it can be kept."""
    if not isinstance(route, dict):
        return ["the answer is not a JSON object"]
    missing = [key for key in ROUTE_SCHEMA["required"] if key not in route]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]
    errors = []
    if route["outcome"] not in OUTCOMES:
        errors.append(f"outcome must be one of {', '.join(OUTCOMES)}")
    haystack = re.sub(r"\s+", " ", trace_text).lower()
    flat = lambda value: re.sub(r"\s+", " ", value or "").strip().lower()  # noqa: E731
    for table in route.get("tables") or []:
        name = flat(table).strip('"')
        if name and name.split(".")[-1] not in haystack:
            errors.append(f"table {table!r} does not appear in the trace")
    sql = route.get("sql")
    if sql and flat(sql) not in haystack and flat(json.dumps(sql))[1:-1] not in haystack:
        errors.append("sql must be copied exactly from the trace")
    probe = route.get("probe")
    if probe and not _READ_ONLY.match(probe.get("sql") or ""):
        errors.append("probe sql must be a read-only query")
    if probe and route["outcome"] != "dead_end" and not any(
            flat(table).strip('"').split(".")[-1] in flat(probe.get("sql")) for table in route.get("tables") or []):
        # A probe over literal values always passes and proves nothing.
        errors.append("an answered route's probe must read one of its tables, or be null")
    if route["outcome"] == "dead_end" and route.get("worth_keeping") and not probe:
        errors.append("a dead end needs a probe that shows the data is still missing")
    return errors


def _clover_complete(messages: list[dict], schema: dict, name: str = "route") -> str:
    import httpx

    base = os.environ.get("RVBBIT_CLOVER_OPENAI_BASE_URL", "http://clover:8090/v1").rstrip("/")
    response = httpx.post(
        base + "/chat/completions", timeout=120,
        headers={"Authorization": "Bearer " + os.environ["RVBBIT_CLOVER_KEY"]},
        json={"model": MODEL, "messages": messages, "temperature": 0,
              "response_format": {"type": "json_schema",
                                  "json_schema": {"name": name, "strict": True, "schema": schema}}})
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def distill(window: dict, *, complete: Callable[[list[dict], dict], str] = _clover_complete) -> dict:
    """The route for one window, or why there is none. One retry on invalid JSON."""
    compacted = compact_trace(window)
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": compacted["text"]}]
    errors: list[str] = []
    retried_for: list[str] = []
    for attempt in (1, 2):
        raw = complete(messages, ROUTE_SCHEMA)
        try:
            route = json.loads(raw)
        except ValueError:
            route, errors = None, ["the answer is not valid JSON"]
        else:
            errors = validate_route(route, compacted["text"])
        if not errors:
            return {"ok": True, "route": route, "attempts": attempt, "retried_for": retried_for,
                    "source": _source(window)}
        retried_for = errors
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Fix these problems and answer again: " + "; ".join(errors)}]
    return {"ok": False, "errors": errors, "attempts": 2, "source": _source(window)}


def _source(window: dict) -> dict:
    turn = window.get("turn") or {}
    return {"hermes_session": window["hermes_session"], "profile": window["profile"],
            "calliope_session": str(turn.get("calliope_session") or "") or None,
            "turn": str(turn.get("id") or "") or None, "asked_by": turn.get("author_display_name"),
            "started_at": str(window["started_at"]), "tool_results": window["tool_results"],
            "seconds": round((window["ended_at"] - window["started_at"]).total_seconds())}


# -- storing, checking and writing back ----------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_routes (
  window_key text PRIMARY KEY,
  status text NOT NULL,
  route jsonb,
  source jsonb NOT NULL,
  audience text[] NOT NULL DEFAULT '{}',
  check_result jsonb,
  checked_at timestamptz,
  hindsight_document text,
  written_at timestamptz,
  errors jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS calliope_routes_status ON rvbbit.calliope_routes (status, updated_at DESC);
ALTER TABLE rvbbit.calliope_routes ADD COLUMN IF NOT EXISTS merged_into text;
"""
# adopted: kept and recalled. skipped/failed: mined, nothing to keep. stale: a
# dead end whose data now exists. broken: an answer whose probe no longer runs.
# unverified: a dead end whose probe could not confirm the data is still missing.
# merged: folded into a newer route for the same question (merged_into).
RECALLED = "adopted"
RECHECKED = ("adopted", "stale", "unverified", "broken")
RECHECK_AFTER = dt.timedelta(hours=24)
RECHECK_PER_TICK = 10


def enabled() -> bool:
    return os.environ.get("CALLIOPE_ROUTES", "on").strip().lower() not in ("off", "0", "false", "no")


def ensure(conn: Any) -> None:
    conn.execute(DDL)


def window_key(window: dict) -> str:
    return f"{window['hermes_session']}:{window['messages'][0]['id']}"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def check(conn_factory: Callable[..., Any], route: dict) -> dict:
    """Run the route's probe under Dreaming's probe safety: the SQL router must call it a
    safe select, it runs read-only with a timeout (and the dream SQL role, if set)."""
    probe = route.get("probe")
    if not probe:
        return {"verdict": "untested", "at": _now().isoformat()}
    from calliope_dreams import _read_probe_rows

    try:
        rows = _read_probe_rows(conn_factory, {"sql": probe["sql"].strip().rstrip(";")})
    except Exception as exc:  # noqa: BLE001 — a probe that fails is a verdict, not a crash
        # An answer whose query no longer runs is broken; a dead end that can't be probed is unknown.
        verdict = "inconclusive" if route["outcome"] == "dead_end" else "contradicted"
        return {"verdict": verdict, "error": f"{type(exc).__name__}: {str(exc)[:240]}", "at": _now().isoformat()}
    if route["outcome"] == "dead_end":
        verdict = "supported" if not rows else "contradicted"  # rows mean the data now exists
    else:
        verdict = "supported" if rows else "inconclusive"
    return {"verdict": verdict, "rows": len(rows), "at": _now().isoformat()}


def status_for(route: dict, result: dict) -> str:
    verdict = result.get("verdict")
    if verdict == "contradicted":
        return "stale" if route["outcome"] == "dead_end" else "broken"
    if route["outcome"] == "dead_end" and verdict != "supported":
        # A dead end tells Callie to stop looking; recall it only while the data is confirmed missing.
        return "unverified"
    return RECALLED


def route_text(route: dict, source: dict, result: dict) -> str:
    """The route as Callie will read it: plain, attributed, dated, with its check."""
    started = str(source.get("started_at") or "")[:10]
    who = source.get("asked_by") or ("a delegated specialist" if source.get("profile") != "default" else "someone")
    outcome = {"answered": "answered", "dead_end": "dead end — the data was not there",
               "corrected_then_answered": "answered after a correction", "abandoned": "abandoned"}[route["outcome"]]
    lines = [f"Route ({outcome}), first worked out {started} for {who}.",
             f"Question: {route['question']}", f"Intent: {route['intent']}", f"Next time: {route['next_time']}"]
    if route.get("tables"):
        lines.append("Tables: " + ", ".join(route["tables"]))
    if route.get("sql"):
        lines.append("Query: " + route["sql"][:1200])
    if route.get("result_summary"):
        lines.append("Result then: " + route["result_summary"])
    lines += ["Gotcha: " + item for item in route.get("gotchas") or []]
    lines += ["Dead end (no need to repeat): " + item for item in route.get("dead_ends") or []]
    verdict = result.get("verdict")
    lines.append(f"Checked {str(result.get('at') or '')[:10]}: " + {
        "supported": "still holds", "inconclusive": "could not confirm", "untested": "no check available",
        "contradicted": "no longer holds"}.get(verdict, "unknown"))
    return "\n".join(lines)


def _hindsight(method: str, path: str, body: Optional[dict] = None, *, timeout: float = 30) -> dict:
    import httpx

    base = os.environ.get("RVBBIT_HINDSIGHT_ENDPOINT", "http://hindsight:8888").rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["RVBBIT_HINDSIGHT_API_KEY"]} \
        if os.environ.get("RVBBIT_HINDSIGHT_API_KEY") else {}
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        response = client.request(method, f"{base}/v1/default/banks/{quote(ROUTES_BANK, safe='')}{path}",
                                  json=body, headers=headers)
    if response.status_code == 404 and method == "POST" and path == "/memories/recall":
        return {"results": []}  # Hindsight 1.x answers 404 for a bank nothing has been written to yet
    response.raise_for_status()
    return response.json() if response.content else {}


_BANK_READY = False


def ensure_bank() -> None:
    """Routes are stored as written (verbatim: Hindsight's LLM only adds dates and
    entities) and never merged into paraphrased observations, which would lose the SQL."""
    global _BANK_READY
    if not _BANK_READY:
        _hindsight("PUT", "", {"retain_extraction_mode": "verbatim", "enable_observations": False})
        _BANK_READY = True


def write_back(key: str, route: dict, source: dict, result: dict, audience: list[str]) -> str:
    ensure_bank()
    document = "route:" + key
    _hindsight("POST", "/memories", {"async": True, "items": [{
        "content": route_text(route, source, result),
        "context": "Calliope route: how an earlier question was answered",
        "timestamp": str(source.get("started_at") or _now().isoformat()).replace(" ", "T"),
        "document_id": document, "update_mode": "replace",
        "tags": ["calliope:processed", "calliope:route", *audience],
        "metadata": {"route_key": key, "outcome": route["outcome"], "verdict": str(result.get("verdict"))},
    }]})
    return document


def retire(document: str) -> None:
    _hindsight("DELETE", "/documents/" + quote(document, safe=""))


def _store(conn: Any, key: str, status: str, source: dict, *, route: Optional[dict] = None,
           audience: Optional[list[str]] = None, result: Optional[dict] = None,
           document: Optional[str] = None, errors: Optional[list[str]] = None) -> None:
    conn.execute("""
        INSERT INTO rvbbit.calliope_routes(window_key,status,route,source,audience,check_result,checked_at,
                                           hindsight_document,written_at,errors)
        VALUES (%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,CASE WHEN %s::text IS NULL THEN NULL ELSE now() END,%s::jsonb)
        ON CONFLICT (window_key) DO UPDATE SET status=EXCLUDED.status, route=EXCLUDED.route,
          source=EXCLUDED.source, audience=EXCLUDED.audience, check_result=EXCLUDED.check_result,
          checked_at=EXCLUDED.checked_at, hindsight_document=EXCLUDED.hindsight_document,
          written_at=EXCLUDED.written_at, errors=EXCLUDED.errors, updated_at=now()""",
        (key, status, json.dumps(route) if route else None, json.dumps(source, default=str), audience or [],
         json.dumps(result) if result else None, (result or {}).get("at"), document, document,
         json.dumps(errors) if errors else None))


def _audience(conn: Any, calliope_session: Optional[str]) -> list[str]:
    if not calliope_session:
        return []
    import calliope_access

    session = (_rows(conn, "SELECT id,owner_email FROM rvbbit.calliope_sessions WHERE id=%s::uuid",
                     (calliope_session,)) or [None])[0]
    return calliope_access.memory_audience(conn, session) if session else []


def mine(conn_factory: Callable[..., Any], *, sessions: Optional[list[str]] = None, limit: int = MAX_PER_TICK,
         settle_seconds: int = SETTLE_SECONDS, complete: Callable[[list[dict], dict], str] = _clover_complete,
         write: bool = True) -> dict:
    """Distil, check, store and write back routes for turns not mined yet."""
    counts = {"candidates": 0, "adopted": 0, "skipped": 0, "failed": 0, "stale": 0, "broken": 0, "unverified": 0}
    with conn_factory() as conn:
        ensure(conn)
        windows = load_windows(conn, sessions=sessions, since=None if sessions else (_now() - LOOKBACK).isoformat())
        known = {r["window_key"] for r in _rows(conn, "SELECT window_key FROM rvbbit.calliope_routes "
                                                      "WHERE window_key = ANY(%s)", ([window_key(w) for w in windows],))}
    settled_before = _now() - dt.timedelta(seconds=settle_seconds)
    fresh = [w for w in windows if window_key(w) not in known and w["ended_at"] <= settled_before][:limit]
    counts["candidates"] = len(fresh)
    for window in fresh:
        key, distilled = window_key(window), distill(window, complete=complete)
        source = distilled["source"]
        with conn_factory() as conn:
            if not distilled["ok"]:
                _store(conn, key, "failed", source, errors=distilled["errors"])
                counts["failed"] += 1
                continue
            route = distilled["route"]
            if not route.get("worth_keeping"):
                _store(conn, key, "skipped", source, route=route)
                counts["skipped"] += 1
                continue
            audience = _audience(conn, source.get("calliope_session"))
        result = check(conn_factory, route)
        status = status_for(route, result)
        if status == RECALLED and write:
            route = merge_earlier(conn_factory, key, route)
        document = write_back(key, route, source, result, audience) if write and status == RECALLED else None
        with conn_factory() as conn:
            _store(conn, key, status, source, route=route, audience=audience, result=result, document=document)
        counts[status] += 1
    return counts


def _union(*lists: list, cap: int = 8) -> list:
    seen, out = set(), []
    for item in (x for items in lists for x in items or []):
        norm = re.sub(r"\s+", " ", str(item)).strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(item)
    return out[:cap]


def merge_earlier(conn_factory: Callable[..., Any], key: str, route: dict) -> dict:
    """Fold earlier adopted routes for the same question into this one: their gotchas and
    dead ends carry over, and they leave recall, so one question keeps one route."""
    try:
        found = _hindsight("POST", "/memories/recall", {
            "query": route["question"][:800], "budget": "low", "max_tokens": 2000,
            "tags": ["calliope:route"], "tags_match": "any_strict"}, timeout=10).get("results") or []
    except Exception:  # noqa: BLE001 — merging is tidiness, never a reason to lose the route
        return route
    keys = [k for k in dict.fromkeys((i.get("metadata") or {}).get("route_key") for i in found) if k and k != key]
    if not keys:
        return route
    with conn_factory() as conn:
        earlier = _rows(conn, "SELECT window_key, route, hindsight_document FROM rvbbit.calliope_routes "
                              "WHERE window_key = ANY(%s) AND status = %s", (keys, RECALLED))
    same = [row for row in earlier if _relevant(conn_factory, route["question"], row["route"])]
    if not same:
        return route
    merged = {**route,
              "gotchas": _union(route.get("gotchas"), *(row["route"].get("gotchas") for row in same)),
              "dead_ends": _union(route.get("dead_ends"), *(row["route"].get("dead_ends") for row in same))}
    for row in same:
        if row["hindsight_document"]:
            try:
                retire(row["hindsight_document"])
            except Exception as exc:  # noqa: BLE001 — the status below already keeps it out of recall
                print(f"memory_routes: retire {row['hindsight_document']} failed: {exc}", file=sys.stderr)
        with conn_factory() as conn:
            conn.execute("UPDATE rvbbit.calliope_routes SET status='merged', merged_into=%s, updated_at=now() "
                         "WHERE window_key=%s", (key, row["window_key"]))
    return merged


def recheck(conn_factory: Callable[..., Any], *, limit: int = RECHECK_PER_TICK, write: bool = True) -> dict:
    """Re-run probes for routes not checked lately, or flagged when their tables changed
    (the discovery trigger clears checked_at). A route that stops holding leaves recall;
    one that holds again comes back."""
    counts = {"rechecked": 0, "changed": 0}
    with conn_factory() as conn:
        ensure(conn)
        due = _rows(conn, """
            SELECT window_key, status, route, source, audience, hindsight_document FROM rvbbit.calliope_routes
             WHERE status = ANY(%s) AND (checked_at IS NULL OR checked_at < now() - %s::interval)
             ORDER BY checked_at NULLS FIRST LIMIT %s""", (list(RECHECKED), f"{int(RECHECK_AFTER.total_seconds())} seconds", limit))
    for row in due:
        result = check(conn_factory, row["route"])
        status = status_for(row["route"], result)
        document = row["hindsight_document"]
        if write and status != row["status"]:
            if status == RECALLED:
                document = write_back(row["window_key"], row["route"], row["source"], result, row["audience"] or [])
            elif document:
                retire(document)
                document = None
            counts["changed"] += 1
        with conn_factory() as conn:
            conn.execute("""UPDATE rvbbit.calliope_routes SET status=%s, check_result=%s::jsonb, checked_at=%s,
                              hindsight_document=%s, updated_at=now() WHERE window_key=%s""",
                         (status, json.dumps(result), result.get("at"), document, row["window_key"]))
        counts["rechecked"] += 1
    return counts


# -- recall ---------------------------------------------------------------------

def _speaker_role(subject: Optional[str]) -> Optional[str]:
    """In pg-auth installs the signed-in subject IS a Postgres role; elsewhere there
    is no per-person data permission to honour."""
    try:
        import auth
        if getattr(auth, "AUTH_MODE", "shared") != "pg":
            return None
        return subject if subject and auth._ROLE_NAME_RE.fullmatch(str(subject)) else None
    except Exception:  # noqa: BLE001 — no auth module, no per-person permissions
        return None


def _readable(conn: Any, role: str, tables: list[str]) -> bool:
    """Every table the route names must be readable by the speaker, without row
    security (whose rows a route's result was drawn from can't be known)."""
    if not tables:
        return True
    row = conn.execute(
        "SELECT bool_and(c.oid IS NOT NULL AND NOT c.relrowsecurity AND has_table_privilege(%s,c.oid,'SELECT')) AS ok "
        "FROM unnest(%s::text[]) t LEFT JOIN pg_class c ON c.oid=to_regclass(t)", (role, tables)).fetchone()
    return bool(row and row["ok"])


RELEVANCE = {
    "same": ("The earlier question asks for the same information as the new one, or a close variant: other "
             "wording, another time period, or another breakdown or filter of the same data. What was learned "
             "answering it would save searching now."),
    "different": ("The earlier question is about different data or a different task, even if they share words "
                  "such as a region, a table or a chart. What was learned answering it would not help now."),
}
RELEVANCE_MIN = 0.6


def _relevant(conn_factory: Callable[..., Any], question: str, route: dict) -> bool:
    """Jev decides whether an earlier route answers the same question; doubt keeps it out."""
    state = {"new_question": question[:800], "earlier_question": route.get("question"),
             "earlier_intent": route.get("intent"), "earlier_tables": route.get("tables") or []}
    try:
        with conn_factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '6s'")
                row = conn.execute("SELECT rvbbit.clover_decide(%s::jsonb, rvbbit.choice_question(%s::jsonb)) AS d",
                                   (json.dumps(state, default=str), json.dumps(RELEVANCE))).fetchone()
    except Exception:  # noqa: BLE001
        return False
    answer = ((row or {}).get("d") or {}).get("answers", {}).get("result", {}) or {}
    probabilities = answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {}
    try:
        same = float(probabilities.get("same", answer.get("confidence") if answer.get("choice") == "same" else 0))
    except (TypeError, ValueError):
        return False
    return answer.get("choice") == "same" and same >= RELEVANCE_MIN


def recall(conn_factory: Callable[..., Any], question: str, *, subject: Optional[str] = None,
           audience: Optional[list[str]] = None, limit: int = RECALL_LIMIT) -> list[dict]:
    """Routes that answer something like ``question`` and that this speaker may see."""
    if not enabled() or not (question or "").strip():
        return []
    found = _hindsight("POST", "/memories/recall", {
        "query": question[:800], "budget": "low", "max_tokens": 2000,
        "tags": ["calliope:route"], "tags_match": "any_strict"}, timeout=6).get("results") or []
    keys = []
    for item in found:
        key = (item.get("metadata") or {}).get("route_key") or str(item.get("document_id") or "").removeprefix("route:")
        if key and key not in keys:
            keys.append(key)
    if not keys:
        return []
    scoped = os.environ.get("CALLIOPE_MEMORY_SCOPE", "shared").strip().lower() not in ("", "shared")
    role = _speaker_role(subject)
    with conn_factory() as conn:
        rows = {r["window_key"]: r for r in _rows(conn, """
            SELECT window_key, route, source, audience, check_result FROM rvbbit.calliope_routes
             WHERE window_key = ANY(%s) AND status = %s""", (keys, RECALLED))}
        eligible = []
        for key in keys[:limit + 2]:
            row = rows.get(key)
            if not row:
                continue  # retired or stale since it was written
            if scoped and audience and not (set(audience) <= set(row["audience"] or [])
                                           or "audience:everyone" in (row["audience"] or [])):
                continue
            if role and not _readable(conn, role, row["route"].get("tables") or []):
                continue
            eligible.append(row)
    if not eligible:
        return []
    # Similar words are not the same question: Jev checks each candidate, in parallel.
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(eligible)) as pool:
        verdicts = list(pool.map(lambda row: _relevant(conn_factory, question, row["route"]), eligible))
    return [row for row, keep in zip(eligible, verdicts) if keep][:limit]


def context_for(conn_factory: Callable[..., Any], session: dict, question: str, subject: Optional[str]) -> str:
    """The block Calliope adds to a turn: earlier routes for questions like this one."""
    try:
        audience = None
        if os.environ.get("CALLIOPE_MEMORY_SCOPE", "shared").strip().lower() not in ("", "shared"):
            with conn_factory() as conn:
                audience = _audience(conn, str(session["id"]))
        routes = recall(conn_factory, question, subject=subject, audience=audience)
    except Exception as exc:  # noqa: BLE001 — memory must never block a turn
        print(f"WARNING: route recall failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return ""
    if not routes:
        return ""
    body = "\n\n".join(route_text(r["route"], r["source"], r["check_result"] or {}) for r in routes)
    return ("[CALLIOPE ROUTES — earlier work on similar questions]\n"
            "Notes from earlier turns, checked against the data since. Start from them instead of "
            "repeating the search; if the question differs or something no longer fits, check the data.\n\n"
            + body + "\n[/CALLIOPE ROUTES]")


# -- worker -----------------------------------------------------------------------

_WORKER_STARTED = False


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _WORKER_STARTED
    if _WORKER_STARTED or not enabled():
        return False

    def _loop() -> None:
        time.sleep(180)
        while True:
            try:
                counts = {**mine(conn_factory), **recheck(conn_factory)}
                if counts["candidates"] or counts["changed"]:
                    print(f"memory_routes: {json.dumps(counts)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"memory_routes tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=_loop, name="memory-routes", daemon=True).start()
    _WORKER_STARTED = True
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--session", action="append", help="Hermes session id (repeatable)")
    parser.add_argument("--since", help="only sessions active since this time")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--min-tool-results", type=int, default=MIN_TOOL_RESULTS)
    parser.add_argument("--trace", action="store_true", help="print the compacted trace instead of distilling")
    parser.add_argument("--mine", action="store_true", help="store, check and write back new routes")
    parser.add_argument("--recheck", action="store_true", help="re-run probes for routes that are due")
    parser.add_argument("--settle", type=int, default=SETTLE_SECONDS, help="seconds a turn must be idle to mine")
    parser.add_argument("--recall", metavar="QUESTION", help="print the route context for a question")
    args = parser.parse_args(argv)
    import psycopg
    from psycopg.rows import dict_row

    def conn_factory():
        return psycopg.connect(os.environ["WAREHOUSE_DSN"], row_factory=dict_row, autocommit=True)

    if args.mine:
        print(json.dumps(mine(conn_factory, sessions=args.session, limit=args.limit, settle_seconds=args.settle)))
        return 0
    if args.recheck:
        print(json.dumps(recheck(conn_factory, limit=args.limit)))
        return 0
    if args.recall:
        print(context_for(conn_factory, {"id": None}, args.recall, None) or "(no routes)")
        return 0
    with conn_factory() as conn:
        windows = load_windows(conn, sessions=args.session, since=args.since,
                               min_tool_results=args.min_tool_results, limit=args.limit)
    for window in windows:
        result = compact_trace(window)["trace"] if args.trace else distill(window)
        print(json.dumps(result, default=str, indent=2), flush=True)
    print(f"{len(windows)} candidate turn(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
