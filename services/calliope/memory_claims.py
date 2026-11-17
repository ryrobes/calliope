"""Claims: what people tell Callie, kept as attributed testimony.

A claim is one thing a person stated that is worth recalling in a later
conversation: a fact about the business or its people, a definition, a
decision, a preference or a plan. It keeps the person's exact words (the
quote), who said it and when, and a standalone version of it with "we",
"she" and "last week" resolved.

Two steps per chat turn:
1. Jev decides whether the turn holds anything worth remembering at all.
   Most turns are questions, requests or thanks and stop here.
2. One LLM call returns the claims as strict JSON. Code checks every claim
   (each quote must appear verbatim in what the person said) and sends the
   answer back once with the problems; claims that still fail are dropped.

When CALLIOPE_MEMORY_EXTRACTOR is calliope (the default), a worker runs this on
every finished chat turn, stores the claims in rvbbit.calliope_claims and writes
each one into Callie's Hindsight bank as written (a verbatim strategy), and
Calliope tells Hermes not to retain the turn itself. Claims marked personal or
restricted are tagged calliope:sensitive with the conversation's audience, so
recall keeps them to the people who could see them. Set it to hindsight to go
back to Hindsight's own extraction.

    python memory_claims.py --eval memory_claims_eval.json     # score the extractor
    python memory_claims.py --turn <calliope turn id>           # extract one turn
    python memory_claims.py --tick                              # process waiting turns now
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

import memory_routes

KINDS = ("business_fact", "person_fact", "definition", "decision", "preference", "plan", "event")
CHECKS = ("data", "calliope", "person", "none")
SENSITIVITY = ("normal", "personal", "restricted")
MAX_CLAIMS = 8

GATE = {
    "remember": ("The person states something worth recalling in a later conversation: a fact about the "
                 "business, its customers or its people; where the company's data lives or how to read it; "
                 "what a term or metric means; a decision, rule, preference or plan; or a correction of "
                 "something said before. A question or request counts too when it states such a fact along "
                 "the way (\"since our fiscal year starts in February, ...\")."),
    "skip": ("Nothing here would matter in a later conversation: a question or request with no new fact, "
             "an instruction about this one task, thanks, a greeting, small talk, or a test message."),
}
GATE_MIN = 0.5
# A skip Jev is unsure of still goes to the extractor, which can return nothing.
GATE_SURE_SKIP = 0.8

CLAIM_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["claims"],
    "properties": {"claims": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["quote", "claim", "subject", "value", "unit", "as_of", "kind", "durable",
                     "check_with", "sensitivity"],
        "properties": {
            "quote": {"type": "string"},
            "claim": {"type": "string"},
            "subject": {"type": "string"},
            "value": {"type": ["string", "null"]},
            "unit": {"type": ["string", "null"]},
            "as_of": {"type": ["string", "null"]},
            "kind": {"type": "string", "enum": list(KINDS)},
            "durable": {"type": "boolean"},
            "check_with": {"type": "string", "enum": list(CHECKS)},
            "sensitivity": {"type": "string", "enum": list(SENSITIVITY)},
        }}}},
}

PROMPT = """You keep a team's memory for an AI data analyst (Callie). From one message a
person sent, list the things they stated that are worth recalling in a later
conversation. Callie's reply is there only to resolve what the message refers to.

For each claim:
- quote: the exact words from the person's message that state it, copied
  character for character (a phrase or sentence, not the whole message).
- claim: one standalone sentence anyone could understand later. Replace "we",
  "our" and "us" with "the company"; resolve "she", "that region" and similar from
  the conversation; turn relative dates ("last week", "next Tuesday") into dates
  using today's date. Do not add anything the person did not say.
- subject: a short noun phrase for what the claim is about.
- value and unit: the stated value when there is one (a number, date, name or
  code), else null.
- as_of: the date the claim is true from or refers to (YYYY-MM-DD), else null.
- kind: business_fact | person_fact | definition | decision | preference | plan | event.
- durable: true if it will still matter next week.
- check_with: data (company data could confirm it), calliope (Calliope's own
  records, such as who is in a room), person (only a person can confirm it), none.
- sensitivity: personal (private to the speaker, e.g. health, job search,
  feelings), restricted (pay, HR, legal or similar), else normal. When the
  person asks to keep the message private ("between us", "don't share this"),
  every claim from it is at least personal.

Skip questions with no new fact, requests about this one task, thanks, greetings
and test messages. An empty list is a good answer when nothing qualifies.
Never record what Callie said, only what the person stated."""


def _norm(text: Any) -> str:
    text = str(text or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip().lower()


def validate_claim(claim: Any, words: str) -> list[str]:
    if not isinstance(claim, dict):
        return ["a claim is not an object"]
    missing = [k for k in CLAIM_SCHEMA["properties"]["claims"]["items"]["required"] if k not in claim]
    if missing:
        return [f"missing fields: {', '.join(missing)}"]
    errors = []
    quote = _norm(claim["quote"])
    if not quote or quote not in _norm(words):
        errors.append(f"quote {claim['quote']!r} is not in the person's message word for word")
    if not str(claim["claim"]).strip():
        errors.append("claim is empty")
    if claim["kind"] not in KINDS:
        errors.append(f"kind must be one of {', '.join(KINDS)}")
    if claim["check_with"] not in CHECKS:
        errors.append(f"check_with must be one of {', '.join(CHECKS)}")
    if claim["sensitivity"] not in SENSITIVITY:
        errors.append(f"sensitivity must be one of {', '.join(SENSITIVITY)}")
    if claim["as_of"] is not None:
        try:
            dt.date.fromisoformat(str(claim["as_of"]))
        except ValueError:
            errors.append(f"as_of {claim['as_of']!r} must be a date as YYYY-MM-DD or null")
    return errors


def _decide(conn_factory: Callable[..., Any], state: dict, categories: dict) -> tuple[str, float]:
    """Jev's choice and its probability; ("", 0) when Jev is away."""
    try:
        with conn_factory() as conn:
            with conn.transaction():
                conn.execute("SET LOCAL statement_timeout = '8s'")
                row = conn.execute("SELECT rvbbit.clover_decide(%s::jsonb, rvbbit.choice_question(%s::jsonb)) AS d",
                                   (json.dumps(state, default=str), json.dumps(categories))).fetchone()
    except Exception:  # noqa: BLE001
        return "", 0.0
    answer = ((row or {}).get("d") or {}).get("answers", {}).get("result", {}) or {}
    choice = str(answer.get("choice") or "")
    probabilities = answer.get("probabilities") if isinstance(answer.get("probabilities"), dict) else {}
    try:
        p = float(probabilities.get(choice, answer.get("confidence") or 0))
    except (TypeError, ValueError):
        p = 0.0
    return choice, p


def gate(conn_factory: Callable[..., Any], turn: dict) -> dict:
    choice, p = _decide(conn_factory, {"speaker": turn["speaker"], "message": turn["words"][:2000],
                                       "callie_replied": (turn.get("reply") or "")[:600]}, GATE)
    keep = (choice == "remember" and p >= GATE_MIN) or (choice == "skip" and p < GATE_SURE_SKIP)
    return {"keep": keep, "choice": choice or "unavailable", "p": round(p, 3)}


def extract(turn: dict, *, complete: Callable[..., str] = memory_routes._clover_complete) -> dict:
    """The person's claims from one turn. One retry with the problems; claims that
    still fail are dropped, never stored half-formed."""
    today = str(turn.get("at") or dt.date.today())[:10]
    payload = {"today": today, "speaker": turn["speaker"], "message": turn["words"][:6000],
               "callie_replied": (turn.get("reply") or "")[:1500]}
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": json.dumps(payload)}]
    retried_for: list[str] = []
    claims: list = []
    for attempt in (1, 2):
        raw = complete(messages, CLAIM_SCHEMA, "claims")
        try:
            claims = json.loads(raw).get("claims") or []
        except (ValueError, AttributeError):
            claims, problems = [], ["the answer is not valid JSON"]
        else:
            problems = [f"claim {i + 1}: {e}" for i, c in enumerate(claims) for e in validate_claim(c, turn["words"])]
            if len(claims) > MAX_CLAIMS:
                problems.append(f"keep at most {MAX_CLAIMS} claims")
        if not problems:
            break
        retried_for = problems
        if attempt == 1:
            messages += [{"role": "assistant", "content": raw},
                         {"role": "user", "content": "Fix these problems and answer again: " + "; ".join(problems)}]
    kept = [c for c in claims if isinstance(c, dict) and not validate_claim(c, turn["words"])][:MAX_CLAIMS]
    return {"claims": [{**c, "speaker": turn["speaker"]} for c in kept], "dropped": len(claims) - len(kept),
            "attempts": attempt, "retried_for": retried_for}


def process(conn_factory: Callable[..., Any], turn: dict, *,
            complete: Callable[..., str] = memory_routes._clover_complete) -> dict:
    decided = gate(conn_factory, turn)
    if not decided["keep"]:
        return {"gate": decided, "claims": []}
    return {"gate": decided, **extract(turn, complete=complete)}


# -- evaluation -----------------------------------------------------------------

def _matches(expected: dict, claim: dict) -> bool:
    text = _norm(" ".join(str(claim.get(k) or "") for k in ("claim", "subject", "value", "unit", "as_of")))
    if not all(_norm(word) in text for word in expected.get("mentions") or []):
        return False
    return not expected.get("kind") or claim.get("kind") == expected["kind"]


def evaluate(conn_factory: Callable[..., Any], cases: list[dict], *,
             complete: Callable[..., str] = memory_routes._clover_complete) -> dict:
    """Score the extractor on labelled turns: does the gate keep the right turns, are
    the expected claims found, how many extra claims and dropped ones are there."""
    report = {"cases": len(cases), "gate_right": 0, "expected": 0, "found": 0, "extra": 0, "dropped": 0,
              "sensitivity_right": 0, "sensitive_cases": 0, "misses": []}
    for case in cases:
        turn = {"speaker": case["speaker"], "words": case["words"], "reply": case.get("reply", ""),
                "at": case.get("at", "2026-10-20")}
        result = process(conn_factory, turn, complete=complete)
        want = case.get("claims") or []
        report["gate_right"] += int(result["gate"]["keep"] == bool(want))
        report["expected"] += len(want)
        report["dropped"] += result.get("dropped", 0)
        claims = list(result["claims"])
        for expected in want:
            hit = next((c for c in claims if _matches(expected, c)), None)
            if hit:
                report["found"] += 1
                claims.remove(hit)
                if expected.get("sensitivity"):
                    report["sensitive_cases"] += 1
                    report["sensitivity_right"] += int(hit["sensitivity"] == expected["sensitivity"])
            else:
                report["misses"].append({"words": case["words"][:90], "expected": expected,
                                         "gate": result["gate"], "got": [c["claim"] for c in result["claims"]]})
        report["extra"] += len(claims)
        if claims:
            report["misses"].append({"words": case["words"][:90], "extra": [c["claim"] for c in claims]})
    return report


# -- writing ----------------------------------------------------------------------

DDL = """
CREATE TABLE IF NOT EXISTS rvbbit.calliope_memory_turns (
  turn_id uuid PRIMARY KEY,
  processed_at timestamptz NOT NULL DEFAULT now(),
  gate jsonb,
  claims int NOT NULL DEFAULT 0,
  dropped int NOT NULL DEFAULT 0,
  attempts int NOT NULL DEFAULT 1,
  error text
);
CREATE TABLE IF NOT EXISTS rvbbit.calliope_claims (
  turn_id uuid NOT NULL,
  n int NOT NULL,
  session_id uuid,
  speaker text,
  speaker_email text,
  said_at timestamptz,
  claim jsonb NOT NULL,
  audience text[] NOT NULL DEFAULT '{}',
  sensitivity text NOT NULL DEFAULT 'normal',
  hindsight_document text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (turn_id, n)
);
"""
SINCE_KEY = "calliope_memory_extractor_since"
BANK = os.environ.get("CALLIOPE_MEMORY_BANK", "hermes")
STRATEGY = "calliope_processed"
SETTLE_SECONDS = 20
TICK_SECONDS = 60
PER_TICK = 20
MAX_ATTEMPTS = 3
SCOPED_TAG, SENSITIVE_TAG = "calliope:scoped", "calliope:sensitive"


def writes() -> bool:
    """Calliope's extractor, not Hindsight's, writes chat memories."""
    return os.environ.get("CALLIOPE_MEMORY_EXTRACTOR", "calliope").strip().lower() not in ("hindsight", "off")


def _scoped() -> bool:
    return os.environ.get("CALLIOPE_MEMORY_SCOPE", "shared").strip().lower() not in ("", "shared")


def tags_for(claim: dict, audience: list[str]) -> tuple[list[str], Any]:
    """Tags and observation scope for one claim. Shared memory merges across the
    bank; a sensitive claim, or any claim when memory is scoped, stays with the
    people who could see the conversation."""
    tags = ["calliope:processed", "calliope:claim", "kind:" + claim["kind"]]
    sensitive = claim.get("sensitivity", "normal") != "normal" and bool(audience)
    if _scoped() and audience:
        tags += [SCOPED_TAG, *audience]
    if sensitive:
        tags += [SENSITIVE_TAG] + ([] if _scoped() else list(audience))
    if _scoped() and audience:
        return tags, [[SCOPED_TAG, *audience]]
    if sensitive:
        return tags, [[SENSITIVE_TAG, *audience]]
    return tags, "shared"


def _hindsight(method: str, path: str, body: Optional[dict] = None) -> dict:
    import httpx
    from urllib.parse import quote

    base = os.environ.get("RVBBIT_HINDSIGHT_ENDPOINT", "http://hindsight:8888").rstrip("/")
    headers = {"Authorization": "Bearer " + os.environ["RVBBIT_HINDSIGHT_API_KEY"]} \
        if os.environ.get("RVBBIT_HINDSIGHT_API_KEY") else {}
    with httpx.Client(timeout=60, trust_env=False) as client:
        response = client.request(method, f"{base}/v1/default/banks/{quote(BANK, safe='')}{path}",
                                  json=body, headers=headers)
    if response.status_code == 404 and method == "POST" and path == "/memories/recall":
        return {"results": []}  # Hindsight 1.x answers 404 for a bank nothing has been written to yet
    response.raise_for_status()
    return response.json() if response.content else {}


_STRATEGY_READY = False


def ensure_strategy() -> None:
    """Claims are stored as written: the bank gets a verbatim strategy that only
    these items name, so Hindsight's own extraction of other memories is unchanged."""
    global _STRATEGY_READY
    if _STRATEGY_READY:
        return
    try:
        config = (_hindsight("GET", "/config").get("config") or {})
    except Exception as exc:  # noqa: BLE001
        if getattr(getattr(exc, "response", None), "status_code", None) != 404:
            raise
        _hindsight("PUT", "", {})  # Hindsight 1.x answers 404 for a bank nothing has been written to yet: create it with defaults
        config = (_hindsight("GET", "/config").get("config") or {})
    strategies = dict(config.get("retain_strategies") or {})
    if strategies.get(STRATEGY) != {"retain_extraction_mode": "verbatim"}:
        strategies[STRATEGY] = {"retain_extraction_mode": "verbatim"}
        _hindsight("PATCH", "/config", {"updates": {"retain_strategies": strategies}})
    _STRATEGY_READY = True


def claim_text(claim: dict, said_at: Any) -> str:
    return f'{claim["speaker"]} said on {str(said_at)[:10]}: "{claim["quote"]}"\n{claim["claim"]}'


def write(turn: dict, claims: list[dict], audience: list[str]) -> list[str]:
    ensure_strategy()
    items, documents = [], []
    for n, claim in enumerate(claims):
        tags, scopes = tags_for(claim, audience)
        document = f"claim:{turn['id']}:{n}"
        documents.append(document)
        items.append({
            "content": claim_text(claim, turn["at"]), "context": "What a person told Callie",
            "timestamp": str(turn["at"]).replace(" ", "T"), "document_id": document, "update_mode": "replace",
            "strategy": STRATEGY, "tags": tags, "observation_scopes": scopes,
            "metadata": {"speaker": claim["speaker"], "kind": claim["kind"], "subject": claim["subject"][:200],
                         "check_with": claim["check_with"], "sensitivity": claim["sensitivity"],
                         "turn_id": str(turn["id"]), "session_id": str(turn["session_id"])},
        })
    if items:
        _hindsight("POST", "/memories", {"async": True, "items": items})
    return documents


def forget(turn_id: Any) -> int:
    """Remove a turn's earlier claims before it is written again. Deleting a
    document also removes the observations built from it; replacing it in place
    keeps them, with whatever audience they had."""
    gone = 0
    for n in range(MAX_CLAIMS):
        try:
            _hindsight("DELETE", f"/documents/claim:{turn_id}:{n}")
            gone += 1
        except Exception as exc:  # noqa: BLE001
            if getattr(getattr(exc, "response", None), "status_code", None) != 404:
                raise
    return gone


def _since(conn: Any) -> str:
    """Turns before the extractor first ran were already remembered by Hindsight."""
    row = conn.execute("SELECT value->>'at' AS at FROM rvbbit.settings WHERE key=%s", (SINCE_KEY,)).fetchone()
    if row and row["at"]:
        return row["at"]
    conn.execute("INSERT INTO rvbbit.settings(key, value, updated_at) VALUES (%s, jsonb_build_object('at', now()), now()) "
                 "ON CONFLICT (key) DO NOTHING", (SINCE_KEY,))
    return conn.execute("SELECT value->>'at' AS at FROM rvbbit.settings WHERE key=%s", (SINCE_KEY,)).fetchone()["at"]


def tick(conn_factory: Callable[..., Any], *, limit: int = PER_TICK,
         complete: Callable[..., str] = memory_routes._clover_complete) -> dict:
    """Process finished chat turns not processed yet."""
    counts = {"turns": 0, "kept": 0, "claims": 0, "errors": 0}
    with conn_factory() as conn:
        conn.execute(DDL)
        since = _since(conn)
        turns = [dict(r) for r in conn.execute("""
            SELECT t.id, t.session_id, t.user_message AS words, t.assistant_message AS reply,
                   coalesce(t.author_display_name, t.author_email, s.owner_email) AS speaker,
                   lower(coalesce(t.author_email, s.owner_email)) AS speaker_email, t.created_at AS at,
                   s.owner_email, coalesce(m.attempts, 0) AS attempts,
                   EXISTS (SELECT 1 FROM rvbbit.calliope_claims c WHERE c.turn_id = t.id) AS written
              FROM rvbbit.calliope_turns t JOIN rvbbit.calliope_sessions s ON s.id = t.session_id
              LEFT JOIN rvbbit.calliope_memory_turns m ON m.turn_id = t.id
             WHERE t.turn_kind = 'chat' AND t.status = 'complete' AND t.created_at >= %s::timestamptz
               AND coalesce(t.completed_at, t.created_at) <= now() - %s::interval
               AND (m.turn_id IS NULL OR (m.error IS NOT NULL AND m.attempts < %s))
             ORDER BY t.created_at LIMIT %s""",
            (since, f"{SETTLE_SECONDS} seconds", MAX_ATTEMPTS, limit)).fetchall()]
    for turn in turns:
        counts["turns"] += 1
        try:
            with conn_factory() as conn:
                audience = memory_routes._audience(conn, str(turn["session_id"]))
        except Exception:  # noqa: BLE001 — no audience, nothing remembered
            audience = []
        if not (turn["words"] or "").strip() or not audience:
            # An untagged memory would be visible to everyone, so none is written.
            skipped = "empty" if audience else "no audience"
            result, error = {"gate": {"keep": False, "choice": skipped}, "claims": []}, None
        else:
            try:
                result, error = process(conn_factory, turn, complete=complete), None
                if turn["attempts"] or turn["written"]:
                    forget(turn["id"])
                documents = write(turn, result["claims"], audience) if result["claims"] else []
                with conn_factory() as conn:
                    conn.execute("DELETE FROM rvbbit.calliope_claims WHERE turn_id=%s", (turn["id"],))
                    for n, (claim, document) in enumerate(zip(result["claims"], documents)):
                        conn.execute("""
                            INSERT INTO rvbbit.calliope_claims(turn_id,n,session_id,speaker,speaker_email,said_at,
                                                               claim,audience,sensitivity,hindsight_document)
                            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                            ON CONFLICT (turn_id, n) DO UPDATE SET speaker=EXCLUDED.speaker,
                              speaker_email=EXCLUDED.speaker_email, claim=EXCLUDED.claim, audience=EXCLUDED.audience,
                              sensitivity=EXCLUDED.sensitivity, hindsight_document=EXCLUDED.hindsight_document""",
                            (turn["id"], n, turn["session_id"], turn["speaker"], turn["speaker_email"], turn["at"],
                             json.dumps(claim), audience, claim["sensitivity"], document))
            except Exception as exc:  # noqa: BLE001 — retried on a later tick, up to MAX_ATTEMPTS
                result, error = {"gate": None, "claims": []}, f"{type(exc).__name__}: {str(exc)[:300]}"
                counts["errors"] += 1
        counts["kept"] += int(bool(result.get("gate") and result["gate"].get("keep")))
        counts["claims"] += len(result["claims"])
        with conn_factory() as conn:
            conn.execute("""
                INSERT INTO rvbbit.calliope_memory_turns(turn_id,gate,claims,dropped,attempts,error)
                VALUES (%s,%s::jsonb,%s,%s,%s,%s)
                ON CONFLICT (turn_id) DO UPDATE SET processed_at=now(), gate=EXCLUDED.gate, claims=EXCLUDED.claims,
                  dropped=EXCLUDED.dropped, attempts=rvbbit.calliope_memory_turns.attempts + 1, error=EXCLUDED.error""",
                (turn["id"], json.dumps(result.get("gate")), len(result["claims"]), result.get("dropped", 0),
                 turn["attempts"] + 1, error))
    return counts


_WORKER_STARTED = False


def start_worker(conn_factory: Callable[..., Any]) -> bool:
    global _WORKER_STARTED
    if _WORKER_STARTED or not writes():
        return False

    def _loop() -> None:
        time.sleep(60)
        while True:
            try:
                counts = tick(conn_factory)
                if counts["turns"]:
                    print(f"memory_claims: {json.dumps(counts)}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                print(f"memory_claims tick failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            time.sleep(TICK_SECONDS)

    threading.Thread(target=_loop, name="memory-claims", daemon=True).start()
    _WORKER_STARTED = True
    return True


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--eval", metavar="CASES", help="score the extractor on a labelled JSON file")
    parser.add_argument("--turn", help="extract one Calliope turn by id")
    parser.add_argument("--tick", action="store_true", help="process waiting chat turns now")
    args = parser.parse_args(argv)
    import psycopg
    from psycopg.rows import dict_row

    def conn_factory():
        return psycopg.connect(os.environ["WAREHOUSE_DSN"], row_factory=dict_row, autocommit=True)

    if args.tick:
        print(json.dumps(tick(conn_factory)))
        return 0
    if args.eval:
        cases = json.loads(open(args.eval, encoding="utf-8").read())
        print(json.dumps(evaluate(conn_factory, cases), indent=2, default=str))
        return 0
    if args.turn:
        with conn_factory() as conn:
            row = conn.execute("SELECT user_message, assistant_message, author_display_name, created_at "
                               "FROM rvbbit.calliope_turns WHERE id=%s::uuid", (args.turn,)).fetchone()
        turn = {"speaker": row["author_display_name"], "words": row["user_message"],
                "reply": row["assistant_message"], "at": row["created_at"]}
        print(json.dumps(process(conn_factory, turn), indent=2, default=str))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
