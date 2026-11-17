"""Bind a turn's prose to the Stage surfaces it talks about.

Ryan's receipt (pilot, 2026-09-20): in thirty days 163 replies said "on the
Stage"; 86 of them had published nothing, so the pointer landed inside the
folded evidence ledger, sometimes among twenty or ninety queries. And the
replies name literal field values ("Fletcher Great Smiles") that sit in one or
more of those results with nothing linking the two.

One index does both jobs. At turn completion the query surfaces' cells become
a turn-scoped vocabulary; the canon reply is matched against it. From that:

- ``values``: the literal cells the reply mentions, each with the surfaces
  that hold it (the chat renders them as chips that open the result).
- ``spotlight``: which surfaces should sit open at the top of the Stage. An
  explicit ``[[stage: …]]`` footer in the canon wins (tool call id, surface
  UUID, or the title she gave the result); otherwise, when the prose points at
  the Stage and nothing was published, the query whose rows carry the most of
  the mentioned values is inferred and marked so.

Everything is stored under ``response_receipt.citations`` on the turn; the
surface rows are never mutated (the ledger stays append-only, and the row
keeps its exploration phase for the ledger).
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable

VERSION = 5

# A final line such as ``[[stage: call_abc, "Google business targets"]]``.
_FOOTER_RE = re.compile(r"(?:\r?\n|^)[ \t]*\[\[\s*stage\s*:\s*(?P<refs>[^\]\n]{1,600})\]\][ \t]*\s*$", re.IGNORECASE)
_POINTER_RE = re.compile(r"\b(?:on|onto|to|see|check|look at|placed on|put on)\s+(?:the\s+)?stage\b", re.IGNORECASE)
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_HASHY_RE = re.compile(r"^[0-9a-f\-]{20,}$", re.IGNORECASE)
# Opaque identifiers (Salesforce 0013p00002EuOTwAAN, API keys): one unbroken
# alphanumeric run with digits in it. Invoice-style codes keep their separators.
_IDENTIFIER_RE = re.compile(r"^(?=.*\d)[A-Za-z0-9]{12,}$")
_NUMBER_RE = re.compile(r"^-?\d{4,}(?:\.\d+)?$")
_NUMERIC_STRING_RE = re.compile(r"^-?\d[\d,]*(?:\.\d+)?$")
_NUMBER_TEXT_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?[kKMB]?$")

# Cells that read as words but never as a pointer to one result.
_STOPLIST = frozenset({
    "true", "false", "null", "none", "yes", "no", "n/a", "unknown", "other", "total", "totals", "count",
    "active", "inactive", "open", "closed", "pending", "complete", "completed", "done", "new", "old",
    "missing", "present", "high", "medium", "low", "error", "ok", "all", "any", "value", "values",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "today", "yesterday", "tomorrow", "week", "month", "year", "quarter", "day", "date", "time",
})

# Bounds so a ninety-query turn stays cheap: cells scanned per surface, values
# kept in the vocabulary, and chips emitted per reply.
_MAX_ROWS_PER_SURFACE = 400
_MAX_COLUMNS = 48
_MAX_VOCABULARY = 6000
_MAX_VALUES_OUT = 40
_MAX_SURFACES_PER_VALUE = 12
_MAX_SPOTLIGHT = 4
_VALUE_MIN_CHARS = 3
_VALUE_MAX_CHARS = 80


# ---------------------------------------------------------------------------
# the footer contract
# ---------------------------------------------------------------------------

def split_refs(text: Any) -> tuple[str, list[str]]:
    """Strip a trailing ``[[stage: …]]`` footer; return the clean prose and its refs."""
    raw = str(text or "")
    match = _FOOTER_RE.search(raw)
    if not match:
        return raw, []
    refs: list[str] = []
    for part in re.split(r"\s*[,;]\s*", match.group("refs")):
        ref = part.strip().strip("\"'“”‘’`").strip()
        if ref and ref.lower() not in {r.lower() for r in refs}:
            refs.append(ref[:160])
    clean = (raw[: match.start()]).rstrip()
    return clean, refs[:8]


def points_at_stage(text: Any) -> bool:
    return bool(_POINTER_RE.search(str(text or "")))


# ---------------------------------------------------------------------------
# the turn-scoped vocabulary
# ---------------------------------------------------------------------------

_DATE_COLUMN_RE = re.compile(r"year|date|yr\b|fiscal|period|quarter|month|week|day", re.IGNORECASE)


def _number_spellings(value: float | int, column: str | None = None) -> list[str]:
    """The ways a reply writes a number it read from a result: plain, with
    thousands separators, and compact (1.4M, 649k). A four-digit integer in a
    date-like column is a year and stays out (it would match half the results
    on the Stage); 1,950 orders in an orders column is a number like any other."""
    if isinstance(value, float) and not value.is_integer():
        if abs(value) >= 1e15:
            return [str(value)]
        plain = f"{value:.2f}".rstrip("0").rstrip(".")
        if len(plain.replace("-", "").replace(".", "")) < 3:
            return []
        separated = f"{value:,.2f}".rstrip("0").rstrip(".")
        return [plain] if separated == plain else [plain, separated]
    whole = int(value)
    if abs(whole) < 1000:
        return []
    if 1900 <= abs(whole) <= 2100 and (column is None or _DATE_COLUMN_RE.search(column)):
        return []
    out = [str(whole), f"{whole:,}"]
    magnitude = abs(whole)
    sign = "-" if whole < 0 else ""
    for unit, label in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "k")):
        if magnitude >= unit:
            compact = Decimal(magnitude) / Decimal(unit)
            one_decimal = str(compact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)).rstrip("0").rstrip(".")
            for spelled in (one_decimal, str(compact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))):
                form = f"{sign}{spelled}{label}"
                if form not in out:
                    out.append(form)
            break
    return out


def _cell_text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return None  # numbers are indexed through _number_spellings
    if isinstance(value, (dict, list)):
        return None
    text = " ".join(str(value).split())
    if not (_VALUE_MIN_CHARS <= len(text) <= _VALUE_MAX_CHARS):
        return None
    if "\n" in text or _HASHY_RE.match(text) or _UUID_RE.match(text) or _IDENTIFIER_RE.match(text):
        return None
    return text


def _worth_a_chip(text: str) -> bool:
    """Only cells that could only mean one thing get to be chips."""
    lowered = text.lower()
    if lowered in _STOPLIST:
        return False
    if _NUMBER_RE.match(text):
        return True
    if not any(ch.isalpha() for ch in text):
        return False
    tokens = text.split()
    if len(tokens) >= 2:
        return True
    if any(ch.isdigit() for ch in text):
        return True
    # a code-shaped token (place_match_status, host-clinic) never occurs in prose by accident
    if len(text) >= 5 and any(sep in text for sep in "_-/."):
        return True
    # a single Title-case or ALL-CAPS word of some length (a surname, a brand)
    return len(text) >= 5 and (text[:1].isupper() or text.isupper())


def index_values(surfaces: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """value (lowercased) → {text, surfaces: {id: hits}, columns: set} over the turn's query rows."""
    vocabulary: dict[str, dict[str, Any]] = {}
    for surface in surfaces:
        if str(surface.get("kind") or "") != "query":
            continue
        payload = surface.get("payload") if isinstance(surface.get("payload"), dict) else {}
        rows = payload.get("rows") if isinstance(payload.get("rows"), list) else []
        sid = str(surface.get("id") or "")
        if not sid or not rows:
            continue
        for row in rows[:_MAX_ROWS_PER_SURFACE]:
            if isinstance(row, dict):
                items = list(row.items())[:_MAX_COLUMNS]
            elif isinstance(row, list):
                items = [(None, cell) for cell in row[:_MAX_COLUMNS]]
            else:
                continue
            for column, cell in items:
                column_name = str(column) if column else None
                if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                    spellings = _number_spellings(cell, column_name)
                elif isinstance(cell, str) and _NUMERIC_STRING_RE.match(cell.strip()):
                    try:
                        spellings = _number_spellings(float(cell.strip().replace(",", "")) if "." in cell else int(cell.strip().replace(",", "")), column_name)
                    except ValueError:
                        spellings = []
                else:
                    text = _cell_text(cell)
                    spellings = [text] if text is not None and _worth_a_chip(text) else []
                for text in spellings:
                    key = text.lower()
                    entry = vocabulary.get(key)
                    if entry is None:
                        if len(vocabulary) >= _MAX_VOCABULARY:
                            continue
                        entry = {"text": text, "surfaces": {}, "columns": set()}
                        vocabulary[key] = entry
                    entry["surfaces"][sid] = entry["surfaces"].get(sid, 0) + 1
                    if column:
                        entry["columns"].add(str(column))
    return vocabulary


def loose_case(text: str) -> bool:
    if _NUMBER_TEXT_RE.match(text):
        return text[-1] in "kKMBmb"   # 1.4M and 1.4m are the same number; digits are exact
    """Multi-word names and code-shaped tokens may be quoted in any case; a lone
    Title-case word ("Linked", "Partner") must appear as the cell spells it, or
    the prose's ordinary use of the word turns into a chip."""
    return len(text.split()) >= 2 or any(ch.isdigit() for ch in text) or any(sep in text for sep in "_-/.")


def _boundary_pattern(text: str) -> re.Pattern[str]:
    escaped = re.escape(text)
    lead = r"(?<![\w.,])" if text[:1].isalnum() else ""
    tail = r"(?![\w])" if text[-1:].isalnum() else ""
    if _NUMBER_TEXT_RE.match(text):
        # "$1,400,000", "1,400,000 rows", never the 1,400,000 inside 21,400,000
        lead = r"(?<![\w.,\d])[$€£]?"
        tail = r"(?![\w.,]\d)(?![\w])"
    return re.compile(lead + escaped + tail, re.IGNORECASE if loose_case(text) else 0)


def match_values(text: Any, vocabulary: dict[str, dict[str, Any]], surfaces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The vocabulary entries the prose mentions, longest first, non-overlapping."""
    prose = str(text or "")
    if not prose.strip() or not vocabulary:
        return []
    row_counts = {str(s.get("id")): _row_count(s) for s in surfaces}
    ordinals = {str(s.get("id")): int(s.get("ordinal") or 0) for s in surfaces}
    taken: list[tuple[int, int]] = []
    out: list[dict[str, Any]] = []
    for key, entry in sorted(vocabulary.items(), key=lambda kv: (-len(kv[0]), kv[0])):
        if len(entry["surfaces"]) > _MAX_SURFACES_PER_VALUE:
            continue
        pattern = _boundary_pattern(entry["text"])
        spans = [m.span() for m in pattern.finditer(prose)]
        spans = [s for s in spans if not any(s[0] < t[1] and t[0] < s[1] for t in taken)]
        if not spans:
            continue
        taken.extend(spans)
        # the result with the fewest rows holds the value most specifically; latest breaks ties
        ordered = sorted(entry["surfaces"], key=lambda sid: (row_counts.get(sid, 10**9), -ordinals.get(sid, 0)))
        out.append({
            "text": entry["text"],
            "loose_case": loose_case(entry["text"]),
            "occurrences": len(spans),
            "surfaces": ordered,
            "columns": sorted(entry["columns"])[:6],
        })
        if len(out) >= _MAX_VALUES_OUT:
            break
    return out


def _row_count(surface: dict[str, Any]) -> int:
    payload = surface.get("payload") if isinstance(surface.get("payload"), dict) else {}
    try:
        if payload.get("row_count") is not None:
            return int(payload.get("row_count"))
    except (TypeError, ValueError):
        pass
    rows = payload.get("rows")
    return len(rows) if isinstance(rows, list) else 0


# ---------------------------------------------------------------------------
# what to open at the top
# ---------------------------------------------------------------------------

def _published(surface: dict[str, Any]) -> bool:
    presentation = surface.get("presentation") if isinstance(surface.get("presentation"), dict) else {}
    if str(presentation.get("phase") or "") == "published":
        return True
    return str(surface.get("kind") or "") in {"board", "chart", "app", "map", "sketch", "image", "document", "answer"}


def _norm(text: Any) -> str:
    """'Complete Order Value', 'complete_order_value' and 'complete-order-value' are one name."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).split())


def _surface_names(surface: dict[str, Any]) -> set[str]:
    """Every name a result answers to: its title, and the batch key it was run under
    (a batched run_sql names each statement; the title is that key prettified)."""
    names = {_norm(surface.get("title"))}
    source = surface.get("source") if isinstance(surface.get("source"), dict) else {}
    args = source.get("args") if isinstance(source.get("args"), dict) else {}
    batch = args.get("batch") if isinstance(args.get("batch"), dict) else {}
    queries = batch.get("queries")
    if isinstance(queries, str):
        try:
            queries = json.loads(queries)
        except ValueError:
            queries = None
    if isinstance(queries, dict):
        own_sql = " ".join(str(args.get("sql") or "").split())
        for key, sql in queries.items():
            if " ".join(str(sql or "").split()) == own_sql:
                names.add(_norm(key))
    names.discard("")
    return names


def resolve_refs(refs: list[str], surfaces: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Explicit refs → surface ids: tool call id or UUID exactly, else the title or
    batch key under any spelling; unresolved refs are kept for the receipt."""
    resolved: list[str] = []
    unresolved: list[str] = []
    for ref in refs:
        needle = ref.strip()
        hit = None
        for surface in surfaces:
            if str(surface.get("tool_call_id") or "") == needle or str(surface.get("id") or "") == needle.lower():
                hit = surface
                break
        if hit is None and _norm(needle):
            named = [s for s in surfaces if _norm(needle) in _surface_names(s)]
            if named:
                hit = sorted(named, key=lambda s: -int(s.get("ordinal") or 0))[0]
        if hit is None:
            unresolved.append(needle)
        else:
            sid = str(hit.get("id"))
            if sid not in resolved:
                resolved.append(sid)
    return resolved[:_MAX_SPOTLIGHT], unresolved


def infer_spotlight(text: Any, surfaces: list[dict[str, Any]], values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """When the prose points at the Stage and nothing was published, pick the query it means."""
    if not points_at_stage(text):
        return []
    if any(_published(s) for s in surfaces):
        return []
    queries = [s for s in surfaces if str(s.get("kind") or "") == "query"]
    if not queries:
        return []
    if len(queries) == 1:
        return [{"surface_id": str(queries[0].get("id")), "reason": "inferred", "score": 1.0}]
    scores: dict[str, float] = {}
    for value in values:
        weight = 0.5 if _NUMBER_TEXT_RE.match(value["text"]) else 1.0
        for sid in value["surfaces"]:
            scores[sid] = scores.get(sid, 0.0) + weight
    if not scores:
        return []
    def rank(sid: str) -> tuple[float, int, int]:
        surface = next((s for s in queries if str(s.get("id")) == sid), None)
        rows = _row_count(surface) if surface else 0
        comparison = 1 if 2 <= rows <= 200 else 0        # a comparison beats a lone number
        return (scores[sid], comparison, int((surface or {}).get("ordinal") or 0))
    best = sorted(scores, key=rank, reverse=True)
    top = best[0]
    if scores[top] < 1.0:
        return []
    return [{"surface_id": top, "reason": "inferred", "score": round(scores[top], 2)}]


def build(text: Any, refs: list[str], surfaces: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole receipt for one turn, from its clean prose, footer refs and surfaces."""
    vocabulary = index_values(surfaces)
    values = match_values(text, vocabulary, surfaces)
    resolved, unresolved = resolve_refs(refs, surfaces)
    if resolved:
        spotlight = [{"surface_id": sid, "reason": "explicit"} for sid in resolved]
    else:
        # A footer that named nothing we hold still says "look here": fall
        # back to what the prose itself points at.
        spotlight = infer_spotlight(text, surfaces, values)
    return {
        "version": VERSION,
        "refs": refs,
        "unresolved_refs": unresolved,
        "spotlight": spotlight,
        "values": values,
        "vocabulary_size": len(vocabulary),
        "query_surfaces": sum(1 for s in surfaces if str(s.get("kind") or "") == "query"),
    }


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------

def _turn_surfaces(conn: Any, turn_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, ordinal, kind, title, tool_call_id, payload, presentation "
        "FROM rvbbit.calliope_surfaces WHERE turn_id=%s::uuid ORDER BY ordinal",
        (turn_id,),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["id"] = str(item.get("id"))
        out.append(item)
    return out


def record(conn_factory: Callable[..., Any], turn_id: str, text: Any, refs: list[str]) -> dict[str, Any] | None:
    """Compute and persist the receipt for a completed turn. Never raises into completion."""
    with conn_factory() as conn:
        surfaces = _turn_surfaces(conn, turn_id)
        receipt = build(text, refs, surfaces)
        conn.execute(
            "UPDATE rvbbit.calliope_turns SET response_receipt="
            "jsonb_set(coalesce(response_receipt,'{}'::jsonb),'{citations}',%s::jsonb,true) WHERE id=%s::uuid",
            (json.dumps(receipt), turn_id),
        )
    return receipt


def attach(conn_factory: Callable[..., Any], turn_id: str, receipt: dict[str, Any] | None) -> dict[str, Any]:
    """Return the receipt with the stored citations on it (for the completion event)."""
    out = dict(receipt or {})
    with conn_factory() as conn:
        row = conn.execute(
            "SELECT response_receipt->'citations' AS citations FROM rvbbit.calliope_turns WHERE id=%s::uuid",
            (turn_id,),
        ).fetchone()
    citations = (row or {}).get("citations") if row else None
    if isinstance(citations, dict):
        out["citations"] = citations
    return out


def backfill(conn_factory: Callable[..., Any], turns: list[dict[str, Any]], cap: int = 6) -> int:
    """Give older completed turns their receipt on read, a few per request.

    Turns that finished before this shipped have no citations; computing them
    lazily means an existing thread lights up the first time it is opened
    without a sweep over every session.
    """
    done = 0
    for turn in turns:
        if done >= cap:
            break
        if str(turn.get("status") or "") not in {"complete", "partial"}:
            continue
        receipt = turn.get("response_receipt") if isinstance(turn.get("response_receipt"), dict) else {}
        existing = receipt.get("citations")
        if isinstance(existing, dict) and int(existing.get("version") or 0) >= VERSION:
            continue
        text = str(turn.get("assistant_message") or "")
        if not text.strip():
            continue
        clean, refs = split_refs(text)
        try:
            citations = record(conn_factory, str(turn["id"]), clean, refs)
        except Exception:
            continue
        turn["response_receipt"] = dict(receipt, citations=citations)
        done += 1
    return done
